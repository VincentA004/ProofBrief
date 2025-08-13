# infra/infra_stack.py

from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_ec2 as ec2,
    aws_s3 as s3,
    aws_sqs as sqs,
    aws_rds as rds,
)
from constructs import Construct

class ProofbriefStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- 1. Network Foundation (VPC) ---
        vpc = ec2.Vpc(self, "ProofBriefVPC", 
            max_azs=2, 
            nat_gateways=1,
            # NEW: Explicitly configure subnet types to include isolated subnets for the database
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="Isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24
                )
            ]
        )

        # --- 2. Core Storage & Queuing ---
        proof_brief_bucket = s3.Bucket(self, "ProofBriefBucket",
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True
        )
        
        # NEW: Create the Dead-Letter Queue to catch failed messages.
        dlq = sqs.Queue(self, "JobDLQ")

        # CHANGED: Configure the main job queue to use the DLQ.
        job_queue = sqs.Queue(self, "JobQueue",
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3, # Tries to process a message 3 times before sending to DLQ
                queue=dlq
            )
        )

        # --- 3. Security Groups ---
        lambda_sg = ec2.SecurityGroup(self, "LambdaSecurityGroup", vpc=vpc)
        db_sg = ec2.SecurityGroup(self, "DatabaseSecurityGroup", vpc=vpc)
        db_sg.add_ingress_rule(
            peer=lambda_sg,
            connection=ec2.Port.tcp(5432)
        )

        # --- 4. Database (Aurora Serverless v2) ---
        db_cluster = rds.DatabaseCluster(self, "Database",
            # CHANGED: Corrected the engine name from 'aurora_postgresql' to 'aurora_postgres'
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_15_3
            ),
            writer=rds.ClusterInstance.serverless_v2("writer"),
            # ... rest of the configuration is correct ...
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[db_sg],
            # FIXED: Changed username from 'proofbrief-db-admin' to 'proofbriefadmin' (no hyphens allowed)
            credentials=rds.Credentials.from_generated_secret("proofbriefadmin")
        )

        # --- Stack Outputs ---
        CfnOutput(self, "S3BucketName", value=proof_brief_bucket.bucket_name)
        CfnOutput(self, "SQSQueueUrl", value=job_queue.queue_url)
        CfnOutput(self, "DeadLetterQueueUrl", value=dlq.queue_url)
        CfnOutput(self, "DatabaseSecretARN", value=db_cluster.secret.secret_arn)
        CfnOutput(self, "LambdaSecurityGroupID", value=lambda_sg.security_group_id)