"""Connect 4 infrastructure stack — all AWS resources in one place."""

from __future__ import annotations

from typing import Final

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_ecs_patterns as ecs_patterns,
)
from aws_cdk import (
    aws_elasticache as elasticache,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_rds as rds,
)
from constructs import Construct

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_NAME: Final[str] = "connect4"
DB_USERNAME: Final[str] = "connect4"
DB_PORT: Final[int] = 5432
REDIS_PORT: Final[int] = 6379
APP_PORT: Final[int] = 8000
GAME_TTL_SECONDS: Final[str] = "86400"


class Connect4Stack(Stack):
    """Full Connect 4 deployment: VPC, RDS, ElastiCache, ECS Fargate (+ALB optional)."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:  # noqa: ANN003
        super().__init__(scope, construct_id, **kwargs)

        # Read deployment mode from CDK context: "free" skips ALB ($0), "standard" includes ALB (~$16/mo)
        free_tier: bool = self.node.try_get_context("free_tier") == "true"

        # ---------------------------------------------------------------
        # 1. VPC — 2 AZs, public + private subnets, no NAT (free tier)
        # ---------------------------------------------------------------
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        # ---------------------------------------------------------------
        # 2. Security groups
        # ---------------------------------------------------------------
        ecs_sg = ec2.SecurityGroup(self, "EcsSg", vpc=vpc, description="ECS Fargate tasks")
        rds_sg = ec2.SecurityGroup(self, "RdsSg", vpc=vpc, description="RDS PostgreSQL")
        redis_sg = ec2.SecurityGroup(self, "RedisSg", vpc=vpc, description="ElastiCache Redis")

        rds_sg.add_ingress_rule(ecs_sg, ec2.Port.tcp(DB_PORT), "ECS to RDS")
        redis_sg.add_ingress_rule(ecs_sg, ec2.Port.tcp(REDIS_PORT), "ECS to Redis")

        # ---------------------------------------------------------------
        # 3. RDS PostgreSQL (free tier — db.t3.micro, 20 GB)
        # ---------------------------------------------------------------
        db_instance = rds.DatabaseInstance(
            self,
            "Postgres",
            instance_identifier="connect4",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_17_7,
            ),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MICRO),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[rds_sg],
            database_name=DB_NAME,
            credentials=rds.Credentials.from_generated_secret(DB_USERNAME),
            allocated_storage=20,
            max_allocated_storage=20,
            multi_az=False,
            deletion_protection=False,
            removal_policy=RemovalPolicy.DESTROY,
            backup_retention=Duration.days(0),
        )

        # ---------------------------------------------------------------
        # 4. ElastiCache Redis (free tier — cache.t3.micro)
        # ---------------------------------------------------------------
        redis_subnet_group = elasticache.CfnSubnetGroup(
            self,
            "RedisSubnetGroup",
            description="Redis subnet group",
            subnet_ids=[s.subnet_id for s in vpc.isolated_subnets],
        )

        redis_cluster = elasticache.CfnCacheCluster(
            self,
            "Redis",
            cache_node_type="cache.t3.micro",
            engine="redis",
            num_cache_nodes=1,
            vpc_security_group_ids=[redis_sg.security_group_id],
            cache_subnet_group_name=redis_subnet_group.ref,
        )
        redis_cluster.add_dependency(redis_subnet_group)

        # ---------------------------------------------------------------
        # 5. ECS Fargate (free tier — 0.25 vCPU, 0.5 GB)
        #    free_tier=true  → no ALB, direct public IP ($0/mo)
        #    free_tier=false → with ALB, stable DNS (~$16/mo)
        # ---------------------------------------------------------------
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, cluster_name="connect4")

        # Build Docker image directly from the project root
        project_root = str(
            cdk.Stack.of(self).node.try_get_context("project_root")
            or str(__import__("pathlib").Path(__file__).resolve().parent.parent)
        )
        image = ecs.ContainerImage.from_asset(
            directory=project_root,
            file="Dockerfile",
            asset_name="connect4-api",
        )

        # Retrieve DB credentials from Secrets Manager
        db_secret = db_instance.secret
        assert db_secret is not None  # Generated secret always exists

        # Redis endpoint: <cluster-id>.xxxxxx.cache.amazonaws.com
        redis_endpoint = redis_cluster.attr_redis_endpoint_address

        # Shared environment and secrets for the container
        container_environment = {
            "REDIS_URL": f"redis://{redis_endpoint}:{REDIS_PORT}",
            "GAME_TTL_SECONDS": GAME_TTL_SECONDS,
            "DB_NAME": DB_NAME,
            "DB_PORT": str(DB_PORT),
        }
        container_secrets = {
            "DB_HOST": ecs.Secret.from_secrets_manager(db_secret, field="host"),
            "DB_USERNAME": ecs.Secret.from_secrets_manager(db_secret, field="username"),
            "DB_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, field="password"),
        }
        log_driver = ecs.LogDrivers.aws_logs(
            stream_prefix="connect4",
            log_retention=logs.RetentionDays.ONE_WEEK,
        )

        if free_tier:
            # No ALB — standalone Fargate service with public IP
            task_definition = ecs.FargateTaskDefinition(
                self,
                "TaskDef",
                cpu=256,
                memory_limit_mib=512,
            )
            task_definition.add_container(
                "connect4-api",
                image=image,
                port_mappings=[ecs.PortMapping(container_port=APP_PORT)],
                environment=container_environment,
                secrets=container_secrets,
                logging=log_driver,
            )

            # Allow inbound HTTP from anywhere (no ALB to filter)
            ecs_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(APP_PORT), "Internet to ECS (free tier)")

            fargate_service = ecs.FargateService(
                self,
                "FreeService",
                cluster=cluster,
                task_definition=task_definition,
                desired_count=1,
                assign_public_ip=True,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                security_groups=[ecs_sg],
            )

            # Grant the task role access to the DB secret
            db_secret.grant_read(task_definition.task_role)
            db_secret.grant_read(task_definition.execution_role)  # type: ignore[arg-type]

        else:
            # Standard mode — ALB in front of Fargate
            alb_service = ecs_patterns.ApplicationLoadBalancedFargateService(
                self,
                "Service",
                cluster=cluster,
                cpu=256,
                memory_limit_mib=512,
                desired_count=1,
                assign_public_ip=True,
                task_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                security_groups=[ecs_sg],
                task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                    image=image,
                    container_port=APP_PORT,
                    environment=container_environment,
                    secrets=container_secrets,
                    log_driver=log_driver,
                ),
            )

            # Health check
            alb_service.target_group.configure_health_check(
                path="/stats",
                interval=Duration.seconds(30),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            )

            fargate_service = alb_service.service

            cdk.CfnOutput(
                self,
                "AlbUrl",
                value=f"http://{alb_service.load_balancer.load_balancer_dns_name}",
            )

        # Allow ECS tasks to reach RDS and Redis in private subnets
        db_instance.connections.allow_from(fargate_service, ec2.Port.tcp(DB_PORT))

        # ---------------------------------------------------------------
        # 6. Outputs
        # ---------------------------------------------------------------
        cdk.CfnOutput(self, "DeploymentMode", value="free-tier (no ALB)" if free_tier else "standard (with ALB)")
        cdk.CfnOutput(self, "RdsEndpoint", value=db_instance.db_instance_endpoint_address)
        cdk.CfnOutput(self, "RedisEndpoint", value=redis_endpoint)
        cdk.CfnOutput(self, "DbSecretArn", value=db_secret.secret_arn)
        if free_tier:
            cdk.CfnOutput(
                self,
                "AccessNote",
                value=f"No ALB — find the task public IP in ECS console, then open http://<task-ip>:{APP_PORT}",
            )
