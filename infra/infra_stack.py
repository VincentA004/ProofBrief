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
    """
    This stack provisions the foundational infrastructure for the ProofBrief application.
    It includes a 'dev_mode' context flag in cdk.json for developer access.
    """
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Read the dev_mode flag from cdk.json ---
        dev_mode = self.node.try_get_context("dev_mode")

        # --- 1. Network Foundation (VPC) ---
        vpc = ec2.Vpc(self, "ProofBriefVPC",
            max_azs=2,
            nat_gateways=1
        )

        # --- 2. Core Storage & Queuing ---
        proof_brief_bucket = s3.Bucket(self, "ProofBriefBucket",
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True # Best practice for data protection
        )

        # Create the Dead-Letter Queue to catch failed messages.
        dlq = sqs.Queue(self, "JobDLQ")

        # Configure the main job queue to use the DLQ.
        job_queue = sqs.Queue(self, "JobQueue",
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3, # Tries a message 3 times before sending to DLQ
                queue=dlq
            )
        )

        # --- 3. Security Groups ---
        lambda_sg = ec2.SecurityGroup(self, "LambdaSecurityGroup",
            vpc=vpc,
            description="Security group for the ProofBrief Lambda functions"
        )

        db_sg = ec2.SecurityGroup(self, "DatabaseSecurityGroup",
            vpc=vpc,
            description="Security group for the Aurora database"
        )

        db_sg.add_ingress_rule(
            peer=lambda_sg,
            connection=ec2.Port.tcp(5432),
            description="Allow inbound connections from Lambdas"
        )

        # If dev_mode is true, add a rule for your local IP address.
        if dev_mode:
            # IMPORTANT: This rule is only for development!
            # Replace YOUR_IP_ADDRESS with your actual public IP.
            db_sg.add_ingress_rule(
                peer=ec2.Peer.ipv4("47.184.59.40/32"),
                connection=ec2.Port.tcp(5432),
                description="Allow local machine access for migrations"
            )

        # --- 4. Database (Aurora Serverless v2) ---
        # Conditionally select the subnet type based on dev_mode.
        db_subnet_type = ec2.SubnetType.PUBLIC if dev_mode else ec2.SubnetType.PRIVATE_ISOLATED

        db_cluster = rds.DatabaseCluster(self, "Database",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_15_3
            ),
            writer=rds.ClusterInstance.serverless_v2("writer"),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=db_subnet_type
            ),
            security_groups=[db_sg],
            credentials=rds.Credentials.from_generated_secret("proofbriefadmin")
        )

        # --- Stack Outputs ---
        CfnOutput(self, "S3BucketName", value=proof_brief_bucket.bucket_name)
        CfnOutput(self, "SQSQueueUrl", value=job_queue.queue_url)
        CfnOutput(self, "DeadLetterQueueUrl", value=dlq.queue_url)
        CfnOutput(self, "DatabaseSecretARN", value=db_cluster.secret.secret_arn)
        CfnOutput(self, "LambdaSecurityGroupID", value=lambda_sg.security_group_id)