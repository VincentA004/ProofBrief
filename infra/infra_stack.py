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

        # --- 2) S3 ---
        bucket = s3.Bucket(
            self,
            "ProofBriefBucket",
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
        )
        bucket.add_cors_rule(
            allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.PUT],
            allowed_origins=[
                "https://main.d15bkn0oauvnds.amplifyapp.com",
                "https://www.proofbrief.com",
                "https://proofbrief.com",
                "http://localhost:8080",
            ],
            allowed_headers=["*"],
            exposed_headers=["ETag"],
            max_age=3000,
        )

        # Optional SQS (not used now)
        dlq = sqs.Queue(self, "JobDLQ")
        job_queue = sqs.Queue(
            self,
            "JobQueue",
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=dlq),
        )

        # --- 3) Security Groups ---
        lambda_sg = ec2.SecurityGroup(self, "LambdaSecurityGroup", vpc=vpc)
        db_sg = ec2.SecurityGroup(self, "DatabaseSecurityGroup", vpc=vpc)
        db_sg.add_ingress_rule(peer=lambda_sg, connection=ec2.Port.tcp(5432))

        # --- 4) Aurora (Data API) ---
        db_cluster = rds.DatabaseCluster(
            self,
            "Database",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_15_3
            ),
            writer=rds.ClusterInstance.serverless_v2("writer"),
            vpc=vpc,
            security_groups=[db_sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            serverless_v2_min_capacity=0.5,
            enable_data_api=True,
            credentials=rds.Credentials.from_generated_secret("proofbriefadmin"),
        )

        # --- 5) Cognito ---
        user_pool = cognito.UserPool(
            self,
            "ProofBriefUserPool",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True, username=False),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        user_pool_client = user_pool.add_client(
            "ProofBriefWebClient",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
            ),
            prevent_user_existence_errors=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(implicit_code_grant=True, authorization_code_grant=True),
                callback_urls=["http://localhost:3000/"],
                logout_urls=["http://localhost:3000/"],
            ),
        )

        # --- 6) Shared Layer ---
        deps_layer = _lambda.LayerVersion(
            self,
            "DepsLayer",
            code=_lambda.Code.from_asset("layer"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_12],
            description="Shared third-party Python deps for Proofbrief Lambdas",
        )

        # --- 7) Lambda env ---
        lambda_env = {
            "S3_BUCKET_NAME": bucket.bucket_name,
            "DB_CLUSTER_ARN": db_cluster.cluster_arn,
            "DB_SECRET_ARN": db_cluster.secret.secret_arn,
            "DB_NAME": os.environ.get("DB_NAME", "postgres"),
            "BEDROCK_MODEL_ID": os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"),
            "GITHUB_SECRET_ARN": os.environ.get("GITHUB_SECRET_ARN", ""),
            "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO"),
            "ALLOW_DEV_NO_AUTH": os.environ.get("ALLOW_DEV_NO_AUTH", "false"),
        }

        # NOTE: Do NOT put memory_size/timeout here; set per-function below
        common_kwargs_base = dict(
            entry="../backend",
            runtime=_lambda.Runtime.PYTHON_3_12,
            environment=lambda_env,
            layers=[deps_layer],
            security_groups=[lambda_sg],
            vpc=vpc,
        )

        # --- 8) Lambdas (with per-fn memory/timeout) ---
        parse_lambda = lambda_python.PythonFunction(
            self, "ParseResumeFn",
            index="functions/parse_resume.py",
            timeout=Duration.seconds(300),
            memory_size=1024,
            **common_kwargs_base,
        )
        process_lambda = lambda_python.PythonFunction(
            self, "ProcessContentFn",
            index="functions/process_content.py",
            timeout=Duration.seconds(300),
            memory_size=1024,
            **common_kwargs_base,
        )
        resume_lambda = lambda_python.PythonFunction(
            self, "ResumeAgentFn",
            index="functions/resume_agent.py",
            timeout=Duration.seconds(300),
            memory_size=1024,
            **common_kwargs_base,
        )
        save_output_lambda = lambda_python.PythonFunction(
            self, "SaveOutputFn",
            index="functions/save_output.py",
            timeout=Duration.seconds(300),
            memory_size=1024,
            **common_kwargs_base,
        )
        api_lambda = lambda_python.PythonFunction(
            self, "ApiHandlerFn",
            index="functions/api.py",
            timeout=Duration.seconds(45),
            memory_size=512,
            **common_kwargs_base,
        )

        # --- 9) IAM perms ---
        def grant_common(fn: _lambda.Function):
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
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[db_cluster.secret.secret_arn],
            ))
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[bucket.bucket_arn],
            ))
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:HeadObject"],
                resources=[f"{bucket.bucket_arn}/*"],
            ))
            if lambda_env["GITHUB_SECRET_ARN"]:
                fn.add_to_role_policy(iam.PolicyStatement(
                    actions=["secretsmanager:GetSecretValue"],
                    resources=[lambda_env["GITHUB_SECRET_ARN"]],
                ))

        for fn in [parse_lambda, process_lambda, resume_lambda, save_output_lambda, api_lambda]:
            grant_common(fn)

        parse_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["textract:StartDocumentTextDetection", "textract:GetDocumentTextDetection"],
            resources=["*"],
        ))
        parse_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetBucketLocation"],
            resources=[bucket.bucket_arn],
        ))
        for fn in [process_lambda, resume_lambda]:
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=["*"],
            ))

        api_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["states:StartExecution"],
            resources=["*"],
        ))

        # --- 10) Step Functions ---
        parse_task = tasks.LambdaInvoke(self, "ParseResumeTask", lambda_function=parse_lambda, output_path="$.Payload")
        process_task = tasks.LambdaInvoke(self, "ProcessContentTask", lambda_function=process_lambda, output_path="$.Payload")
        resume_task = tasks.LambdaInvoke(self, "ResumeAgentTask", lambda_function=resume_lambda, output_path="$.Payload")
        save_output_task = tasks.LambdaInvoke(self, "SaveOutputTask", lambda_function=save_output_lambda, output_path="$.Payload")
        chain = parse_task.next(process_task).next(resume_task).next(save_output_task)

        sm_log_group = logs.LogGroup(
            self, "StateMachineLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        state_machine = sfn.StateMachine(
            self, "ProofBriefPipeline",
            definition_body=sfn.DefinitionBody.from_chainable(chain),
            timeout=Duration.minutes(10),
            logs=sfn.LogOptions(destination=sm_log_group, level=sfn.LogLevel.ERROR),
        )

        api_lambda.add_environment("STATE_MACHINE_ARN", state_machine.state_machine_arn)
        api_lambda.role.add_to_principal_policy(iam.PolicyStatement(
            actions=["states:StartExecution"],
            resources=[state_machine.state_machine_arn],
        ))

        # --- 11) API Gateway + Cognito authorizer ---
        api = apigw.RestApi(
            self, "ProofBriefApi",
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=["Content-Type", "Authorization"],
            ),
            deploy_options=apigw.StageOptions(stage_name="prod"),
        )

        authorizer = apigw.CognitoUserPoolsAuthorizer(
            self, "ProofBriefAuthorizer",
            cognito_user_pools=[user_pool],
        )

        integration = apigw.LambdaIntegration(api_lambda, proxy=True)

        briefs = api.root.add_resource("briefs")
        briefs.add_method("POST", integration, authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        briefs.add_method("GET", integration, authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        brief_id = briefs.add_resource("{id}")
        brief_id.add_method("GET", integration, authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        brief_id.add_method("DELETE", integration, authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        start = brief_id.add_resource("start")
        start.add_method("PUT", integration, authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # --- 12) Outputs ---
        CfnOutput(self, "S3BucketName", value=bucket.bucket_name)
        CfnOutput(self, "SQSQueueUrl", value=job_queue.queue_url)
        CfnOutput(self, "DatabaseClusterARN", value=db_cluster.cluster_arn)
        CfnOutput(self, "DatabaseSecretARN", value=db_cluster.secret.secret_arn)
        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        CfnOutput(self, "ApiGatewayUrl", value=api.url)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=user_pool_client.user_pool_client_id)
