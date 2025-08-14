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
)
from constructs import Construct
import os


class ProofbriefStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- 1. VPC ---
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

        # --- 2. S3 + SQS ---
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

        # --- 3. Security Groups (for future VPC lambdas if needed) ---
        lambda_sg = ec2.SecurityGroup(self, "LambdaSecurityGroup", vpc=vpc)
        db_sg = ec2.SecurityGroup(self, "DatabaseSecurityGroup", vpc=vpc)
        db_sg.add_ingress_rule(peer=lambda_sg, connection=ec2.Port.tcp(5432))

        # --- 4. Aurora Serverless v2 (with Data API) ---
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

        # --- 5. Lambda common config ---
        lambda_env = {
            # NOTE: Do not set AWS_REGION/AWS_DEFAULT_REGION here (reserved by Lambda runtime)
            "S3_BUCKET_NAME": bucket.bucket_name,
            "DB_CLUSTER_ARN": db_cluster.cluster_arn,
            "DB_SECRET_ARN": db_cluster.secret.secret_arn,
            "DB_NAME": os.environ.get("DB_NAME", "postgres"),
            # Optional: model id for Bedrock (used by process/resume lambdas)
            "BEDROCK_MODEL_ID": os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"),
            # Optional: GitHub token secret (if you created one)
            "GITHUB_SECRET_ARN": os.environ.get("GITHUB_SECRET_ARN", ""),
        }

        common_kwargs = dict(
            entry="../backend",   # bundle the whole backend folder (contains functions/shared/requirements)
            runtime=_lambda.Runtime.PYTHON_3_12,
            timeout=Duration.seconds(60),
            memory_size=1024,
            environment=lambda_env,
        )

        # --- 6. Lambdas ---
        parse_lambda = lambda_python.PythonFunction(
            self, "ParseResumeFn", index="functions/parse_resume.py", **common_kwargs
        )
        process_lambda = lambda_python.PythonFunction(
            self, "ProcessContentFn", index="functions/process_content.py", **common_kwargs
        )
        resume_lambda = lambda_python.PythonFunction(
            self, "ResumeAgentFn", index="functions/resume_agent.py", **common_kwargs
        )
        save_output_lambda = lambda_python.PythonFunction(
            self, "SaveOutputFn", index="functions/save_output.py", **common_kwargs
        )

        # --- 7. Permissions for Data API + Secrets + S3 ---
        for fn in [parse_lambda, process_lambda, resume_lambda, save_output_lambda]:
            # Aurora Data API
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=[
                        "rds-data:ExecuteStatement",
                        "rds-data:BatchExecuteStatement",
                        "rds-data:BeginTransaction",
                        "rds-data:CommitTransaction",
                        "rds-data:RollbackTransaction",
                    ],
                    resources=[db_cluster.cluster_arn],
                )
            )
            # Read DB credentials
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["secretsmanager:GetSecretValue"],
                    resources=[db_cluster.secret.secret_arn],
                )
            )
            # S3 read/write (SaveOutput needs PutObject; others read/write processed artifacts)
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=[
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:ListBucket",
                    ],
                    resources=[bucket.bucket_arn, f"{bucket.bucket_arn}/*"],
                )
            )
            # (Optional) GitHub secret access if configured
            if lambda_env["GITHUB_SECRET_ARN"]:
                fn.add_to_role_policy(
                    iam.PolicyStatement(
                        actions=["secretsmanager:GetSecretValue"],
                        resources=[lambda_env["GITHUB_SECRET_ARN"]],
                    )
                )

        # --- 8. Step Functions: parse -> process -> resume -> save_output ---
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

        # CloudWatch log group for state machine
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

        # --- 9. API Gateway to trigger the pipeline ---
        api = apigw.RestApi(self, "ProofBriefApi")
        start_res = api.root.add_resource("start")

        # Simple request: expects { "briefId": "<uuid>" }
        start_integration = apigw.AwsIntegration(
            service="states",
            action="StartExecution",
            integration_http_method="POST",
            options=apigw.IntegrationOptions(
                credentials_role=iam.Role(
                    self,
                    "ApiGatewayStatesRole",
                    assumed_by=iam.ServicePrincipal("apigateway.amazonaws.com"),
                    inline_policies={
                        "AllowStartExecution": iam.PolicyDocument(
                            statements=[
                                iam.PolicyStatement(
                                    actions=["states:StartExecution"],
                                    resources=[state_machine.state_machine_arn],
                                )
                            ]
                        )
                    },
                ),
                request_templates={
                    "application/json": (
                        "{"
                        f"\"stateMachineArn\": \"{state_machine.state_machine_arn}\","
                        "\"input\": \"$util.escapeJavaScript($input.body)\""
                        "}"
                    )
                },
                integration_responses=[
                    apigw.IntegrationResponse(status_code="200"),
                ],
            ),
        )

        start_res.add_method(
            "POST",
            start_integration,
            method_responses=[apigw.MethodResponse(status_code="200")],
        )

        # --- 10. Outputs ---
        CfnOutput(self, "S3BucketName", value=bucket.bucket_name)
        CfnOutput(self, "SQSQueueUrl", value=job_queue.queue_url)
        CfnOutput(self, "DatabaseClusterARN", value=db_cluster.cluster_arn)
        CfnOutput(self, "DatabaseSecretARN", value=db_cluster.secret.secret_arn)
        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        CfnOutput(self, "ProofBriefApiEndpoint", value=api.url)
        CfnOutput(self, "ApiUrl", value=f"{api.url}start")
