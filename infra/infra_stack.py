# infra/infra_stack.py

from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    Duration,
    aws_ec2 as ec2,
    aws_s3 as s3,
    aws_iam as iam,
    aws_logs as logs,
    aws_sqs as sqs,
    aws_rds as rds,
    aws_lambda as _lambda,
    aws_lambda_python_alpha as lambda_python,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_apigateway as apigw,
    aws_cognito as cognito,
)
from constructs import Construct
import os


class ProofbriefStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- 1) VPC ---
        vpc = ec2.Vpc(
            self,
            "ProofBriefVPC",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC),
                ec2.SubnetConfiguration(name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                ec2.SubnetConfiguration(name="Isolated", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            ],
        )

        # --- 2) S3 + optional DLQ (SQS kept for future) ---
        bucket = s3.Bucket(
            self,
            "ProofBriefBucket",
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
        )

        dlq = sqs.Queue(self, "JobDLQ")
        job_queue = sqs.Queue(
            self,
            "JobQueue",
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=dlq),
        )

        # --- 3) Security Groups (DB <-> Lambdas) ---
        lambda_sg = ec2.SecurityGroup(self, "LambdaSecurityGroup", vpc=vpc)
        db_sg = ec2.SecurityGroup(self, "DatabaseSecurityGroup", vpc=vpc)
        db_sg.add_ingress_rule(peer=lambda_sg, connection=ec2.Port.tcp(5432))

        # --- 4) Aurora Serverless v2 (Data API enabled) ---
        db_cluster = rds.DatabaseCluster(
            self,
            "Database",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_15_3
            ),
            writer=rds.ClusterInstance.serverless_v2("writer"),
            vpc=vpc,
            serverless_v2_min_capacity=0.5,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[db_sg],
            credentials=rds.Credentials.from_generated_secret("proofbriefadmin"),
            enable_data_api=True,
        )

        # --- 5) Common Lambda env ---
        lambda_env = {
            "S3_BUCKET_NAME": bucket.bucket_name,
            "DB_CLUSTER_ARN": db_cluster.cluster_arn,
            "DB_SECRET_ARN": db_cluster.secret.secret_arn,
            "DB_NAME": os.environ.get("DB_NAME", "postgres"),
            "BEDROCK_MODEL_ID": os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"),
            "GITHUB_SECRET_ARN": os.environ.get("GITHUB_SECRET_ARN", ""),  # optional
        }

        # Shared layer for third-party deps (place built deps in ./layer/python)
        deps_layer = _lambda.LayerVersion(
            self, "DepsLayer",
            code=_lambda.Code.from_asset("layer"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_12],
            description="Shared third-party Python deps for Proofbrief Lambdas",
        )

        common_kwargs = dict(
            entry="../backend",                      # bundles backend/ (functions/, shared/)
            runtime=_lambda.Runtime.PYTHON_3_12,
            timeout=Duration.seconds(300),
            memory_size=1024,
            environment=lambda_env,
            layers=[deps_layer],
            security_groups=[lambda_sg],
            vpc=vpc,
        )

        # --- 6) Lambdas (pipeline) ---
        parse_lambda = lambda_python.PythonFunction(
            self, "ParseResumeFn",
            index="functions/parse_resume.py",
            **common_kwargs,
        )
        process_lambda = lambda_python.PythonFunction(
            self, "ProcessContentFn",
            index="functions/process_content.py",
            **common_kwargs,
        )
        resume_lambda = lambda_python.PythonFunction(
            self, "ResumeAgentFn",
            index="functions/resume_agent.py",
            **common_kwargs,
        )
        save_output_lambda = lambda_python.PythonFunction(
            self, "SaveOutputFn",
            index="functions/save_output.py",
            **common_kwargs,
        )

        # --- 7) API Lambda (handles /briefs endpoints) ---
        api_lambda = lambda_python.PythonFunction(
            self, "ApiHandlerFn",
            index="functions/api.py",  # you provide this file; uses Lambda proxy to route methods/paths
            **common_kwargs,
        )

        # --- 8) IAM perms for Lambdas ---
        for fn in [parse_lambda, process_lambda, resume_lambda, save_output_lambda, api_lambda]:
            # Aurora Data API
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=[
                    "rds-data:ExecuteStatement",
                    "rds-data:BatchExecuteStatement",
                    "rds-data:BeginTransaction",
                    "rds-data:CommitTransaction",
                    "rds-data:RollbackTransaction",
                ],
                resources=[db_cluster.cluster_arn],
            ))
            # Read DB secret
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[db_cluster.secret.secret_arn],
            ))
            # S3 list/get/put inside bucket
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[bucket.bucket_arn],
            ))
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:HeadObject"],
                resources=[f"{bucket.bucket_arn}/*"],
            ))
            # Optional GitHub secret (if env provided)
            if lambda_env["GITHUB_SECRET_ARN"]:
                fn.add_to_role_policy(iam.PolicyStatement(
                    actions=["secretsmanager:GetSecretValue"],
                    resources=[lambda_env["GITHUB_SECRET_ARN"]],
                ))

        # Textract (parse_resume)
        parse_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["textract:StartDocumentTextDetection", "textract:GetDocumentTextDetection"],
            resources=["*"],
        ))
        parse_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetBucketLocation"],
            resources=[bucket.bucket_arn],
        ))

        # Bedrock (process_content + resume_agent)
        for fn in [process_lambda, resume_lambda]:
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=["*"],
            ))

        # API Lambda needs to be able to start executions + describe
        api_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["states:StartExecution", "states:DescribeExecution", "states:DescribeStateMachine"],
            resources=["*"],  # you can scope to this SM ARN below if you like
        ))

        # --- 9) Step Functions chain ---
        parse_task = tasks.LambdaInvoke(
            self,
            "ParseResumeTask",
            lambda_function=parse_lambda,
            output_path="$.Payload",
        )
        process_task = tasks.LambdaInvoke(
            self,
            "ProcessContentTask",
            lambda_function=process_lambda,
            output_path="$.Payload",
        )
        resume_task = tasks.LambdaInvoke(
            self,
            "ResumeAgentTask",
            lambda_function=resume_lambda,
            output_path="$.Payload",
        )
        save_output_task = tasks.LambdaInvoke(
            self,
            "SaveOutputTask",
            lambda_function=save_output_lambda,
            output_path="$.Payload",
        )

        chain = parse_task.next(process_task).next(resume_task).next(save_output_task)

        sm_log_group = logs.LogGroup(
            self,
            "StateMachineLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        state_machine = sfn.StateMachine(
            self,
            "ProofBriefPipeline",
            definition_body=sfn.DefinitionBody.from_chainable(chain),
            timeout=Duration.minutes(10),
            logs=sfn.LogOptions(
                destination=sm_log_group,
                level=sfn.LogLevel.ERROR,
            ),
        )

        # Expose SM ARN to API Lambda via env (so it can StartExecution directly)
        api_lambda.add_environment("STATE_MACHINE_ARN", state_machine.state_machine_arn)

        # --- 10) Cognito for API auth ---
        user_pool = cognito.UserPool(
            self, "ProofBriefUserPool",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True, username=False),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
        )

        user_pool_client = user_pool.add_client(
            "ProofBriefWebClient",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
                admin_user_password=True,
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True, implicit_code_grant=True),
                callback_urls=["http://localhost:3000/"],
                logout_urls=["http://localhost:3000/"],
            ),
        )

        # --- 11) API Gateway (Lambda proxy + Cognito authorizer) ---
        api = apigw.RestApi(self, "ProofBriefApi")

        authorizer = apigw.CognitoUserPoolsAuthorizer(
            self, "ProofBriefAuthorizer",
            cognito_user_pools=[user_pool],
        )

        # Resources
        briefs = api.root.add_resource("briefs")
        briefs.add_cors_preflight(
            allow_origins=apigw.Cors.ALL_ORIGINS,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization"],
        )

        brief_id = briefs.add_resource("{id}")
        brief_id.add_cors_preflight(
            allow_origins=apigw.Cors.ALL_ORIGINS,
            allow_methods=["GET", "PUT", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization"],
        )

        start_res = brief_id.add_resource("start")
        start_res.add_cors_preflight(
            allow_origins=apigw.Cors.ALL_ORIGINS,
            allow_methods=["PUT", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization"],
        )

        # Methods (all protected by Cognito)
        for method, resource in [
            ("POST", briefs),
            ("GET", briefs),
            ("GET", brief_id),
            ("PUT", start_res),
        ]:
            resource.add_method(
                method,
                apigw.LambdaIntegration(api_lambda),  # Lambda proxy handler inspects path/method
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=authorizer,
            )

        # --- 12) Outputs ---
        CfnOutput(self, "S3BucketName", value=bucket.bucket_name)
        CfnOutput(self, "SQSQueueUrl", value=job_queue.queue_url)
        CfnOutput(self, "DatabaseClusterARN", value=db_cluster.cluster_arn)
        CfnOutput(self, "DatabaseSecretARN", value=db_cluster.secret.secret_arn)
        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        CfnOutput(self, "ProofBriefApiEndpoint", value=api.url)
        CfnOutput(self, "ApiUrl", value=f"{api.url}briefs")
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=user_pool_client.user_pool_client_id)
