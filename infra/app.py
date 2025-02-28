"""CDK app entry point — deploys the Connect 4 stack."""

from __future__ import annotations

import os

import aws_cdk as cdk
from stack import Connect4Stack

app = cdk.App()

Connect4Stack(
    app,
    "Connect4Stack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT", "354507107477"),
        region=os.environ.get("CDK_DEFAULT_REGION", "eu-central-1"),
    ),
    description="Connect 4 Real-Time Prototype — ECS Fargate + RDS + ElastiCache",
)

app.synth()
