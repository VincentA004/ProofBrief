#!/usr/bin/env python3

# infra/app.py

import aws_cdk as cdk
import os # NEW: Import the os library
from infra_stack import ProofbriefStack

# NEW: Define the deployment environment using your local AWS CLI configuration.
# This is a best practice for enabling advanced CDK features.
env = cdk.Environment(
    account=os.getenv('CDK_DEFAULT_ACCOUNT'),
    region=os.getenv('CDK_DEFAULT_REGION')
)

app = cdk.App()
ProofbriefStack(app, "ProofbriefStack", env=env) # CHANGED: pass the env

app.synth()