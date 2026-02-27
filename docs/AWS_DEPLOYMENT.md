# AWS Deployment Guide — Connect 4 Real-Time Prototype

> One-command deployment to AWS Free Tier using **AWS CDK** (Infrastructure as Code).
> All resources — VPC, RDS, ElastiCache, ECS Fargate, ALB — are defined in `infra/stack.py`.
>
> For architecture details, AWS service choices, cost breakdown, and scaling plans, see [TECHNICAL_DECISIONS.md](TECHNICAL_DECISIONS.md).

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Deploy with CDK (Local)](#2-deploy-with-cdk-local)
3. [Deploy with GitHub Actions (Recommended)](#3-deploy-with-github-actions-recommended)
4. [Environment Variables](#4-environment-variables)
5. [Monitoring & Logs](#5-monitoring--logs)
6. [Key Files](#6-key-files)

---

## 1. Prerequisites

### For local CDK deployment

- **AWS CLI v2** configured with credentials
- **Node.js 22+** (CDK CLI runs on Node)
- **Python 3.13+**
- **Docker** (CDK builds the container image automatically)

```bash
# Verify tools
aws --version
node --version
python3 --version
docker --version

# Verify AWS credentials
aws sts get-caller-identity
```

### Install CDK CLI and Python dependencies

```bash
npm install -g aws-cdk
pip install -r infra/requirements.txt
```

### For GitHub Actions deployment

No local tools needed — just configure repository secrets (see section 5).

---

## 2. Deploy with CDK (Local)

CDK provisions **everything** in a single command: VPC, security groups, RDS, ElastiCache, ECR image, ECS Fargate service, and (optionally) ALB.

### Deployment Modes

| Mode | Flag | Cost | Access |
|------|------|------|--------|
| **Free tier** | `-c free_tier=true` | **$0/mo** | Task public IP (changes on restart) |
| **Standard** | (default) | **~$16/mo** | Stable ALB DNS name |

### 2.1 Bootstrap CDK (first time per account/region)

```bash
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export CDK_DEFAULT_REGION="eu-central-1"

cdk bootstrap aws://$CDK_DEFAULT_ACCOUNT/$CDK_DEFAULT_REGION
```

### 2.2 Preview changes

```bash
# Free tier ($0/mo — no ALB)
cdk diff -c free_tier=true

# Standard (~$16/mo — with ALB)
cdk diff
```

### 2.3 Deploy

```bash
# Free tier ($0/mo — no ALB)
cdk deploy --require-approval never --outputs-file cdk-outputs.json -c free_tier=true

# Standard (~$16/mo — with ALB)
cdk deploy --require-approval never --outputs-file cdk-outputs.json
```

This takes ~10-15 minutes on first deploy. CDK will:

1. Create a VPC with 2 AZs (public + private isolated subnets, no NAT)
2. Create security groups (ECS, RDS, Redis) with scoped ingress rules
3. Launch RDS PostgreSQL `db.t3.micro` with auto-generated credentials in Secrets Manager
4. Launch ElastiCache Redis `cache.t3.micro`
5. Build the Docker image from the project root and push to ECR
6. Create an ECS Fargate service (with ALB in standard mode, without in free tier)
7. Run Alembic migrations on container startup via `entrypoint.sh`

### 2.4 Check outputs

```bash
cat cdk-outputs.json | python3 -m json.tool
```

Outputs include:

- **AlbUrl** — the application URL (standard mode only)
- **DeploymentMode** — which mode was deployed
- **RdsEndpoint** — PostgreSQL hostname
- **RedisEndpoint** — Redis hostname
- **DbSecretArn** — Secrets Manager ARN for DB credentials

> **Free tier access**: In free tier mode there's no ALB URL. Find the task's public IP in the ECS console (Cluster → Service → Task → Network) and open `http://<task-ip>:8000`. The IP changes on each task restart.

### 2.5 Tear down

```bash
cdk destroy --force
```

> This removes **all** resources. RDS data will be lost (`RemovalPolicy.DESTROY` is set for dev).

---

## 3. Deploy with GitHub Actions (Recommended)

A manual-trigger workflow is provided at `.github/workflows/deploy.yml`.

### 3.1 Configure repository secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret                  | Description                        | Example              |
|-------------------------|------------------------------------|----------------------|
| `AWS_ACCESS_KEY_ID`     | IAM user access key                | `AKIA...`            |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key                | `wJalr...`           |
| `AWS_ACCOUNT_ID`        | 12-digit AWS account number        | `123456789012`       |

> The IAM user needs `AdministratorAccess` or equivalent CDK permissions. For production, scope down to the specific services used.

### 3.2 Run the workflow

1. Go to **Actions** → **Deploy to AWS**
2. Click **Run workflow**
3. Select:
   - **action**: `deploy` or `destroy`
   - **region**: defaults to `eu-central-1`
   - **free_tier**: `true` ($0/mo, no ALB) or `false` (~$16/mo, with ALB)
4. Click **Run workflow** again

The workflow will:

- Bootstrap CDK (idempotent — safe to run repeatedly)
- Show a diff of changes
- Deploy all infrastructure
- Print the ALB URL in the job output
- Upload `cdk-outputs.json` as a build artifact

---

## 4. Environment Variables

CDK injects these into the ECS task at runtime:

| Variable      | Source                              | Example Value                                  |
|---------------|-------------------------------------|------------------------------------------------|
| `DB_HOST`     | Secrets Manager (secret field)      | `connect4-db.abc123.rds.amazonaws.com`         |
| `DB_USERNAME` | Secrets Manager (secret field)      | `connect4`                                     |
| `DB_PASSWORD` | Secrets Manager (secret field)      | (auto-generated)                               |
| `DB_NAME`     | Environment variable                | `connect4`                                     |
| `DB_PORT`     | Environment variable                | `5432`                                         |
| `REDIS_URL`   | Environment variable                | `redis://connect4-redis.abc.cache.amazonaws.com:6379` |
| `GAME_TTL_SECONDS` | Environment variable           | `86400`                                        |

The `entrypoint.sh` script builds `DATABASE_URL` from these components, runs Alembic migrations, and starts Uvicorn.

---

## 5. Monitoring & Logs

| What               | Where                                       |
|---------------------|---------------------------------------------|
| Application logs    | CloudWatch Logs (stream prefix `connect4`)  |
| RDS metrics         | CloudWatch (CPU, connections, IOPS)         |
| Redis metrics       | CloudWatch (memory, cache hits, evictions)  |
| App health          | ALB health check → `GET /docs` (200 OK)    |

---

## 6. Key Files

| File                               | Purpose                                              |
|------------------------------------|------------------------------------------------------|
| `infra/stack.py`                   | CDK stack — all AWS resources                        |
| `infra/app.py`                     | CDK entry point — account/region from env vars       |
| `infra/requirements.txt`           | CDK Python dependencies                             |
| `cdk.json`                         | CDK configuration                                   |
| `.github/workflows/deploy.yml`     | Manual GitHub Actions deploy/destroy workflow        |
| `entrypoint.sh`                    | Container startup — builds DATABASE_URL, runs Alembic |
| `Dockerfile`                       | Container image definition                           |
