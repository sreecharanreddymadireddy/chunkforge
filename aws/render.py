"""Render account-specific ECS/IAM documents locally; never create cloud resources."""

import json
import os
import re
from pathlib import Path
from typing import Any


def required(name: str, pattern: str) -> str:
    """Fail before rendering when an operator input is absent or malformed."""
    value = os.environ.get(name, "")
    if not re.fullmatch(pattern, value):
        raise ValueError(f"Set a valid {name}; see aws/DEPLOY.md")
    return value


def main() -> None:
    """Produce complete task, execution policy, S3 policy and service trust JSON."""
    account = required("CF_ACCOUNT", r"\d{12}")
    region = required("CF_REGION", r"[a-z]{2}-[a-z]+-\d")
    tag = required("CF_TAG", r"[A-Za-z0-9_.-]{1,128}")
    bucket = required("CF_BUCKET", r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
    secret = required("CF_DSN_SECRET_ARN", rf"arn:aws:secretsmanager:{region}:{account}:secret:.+")
    kms = os.environ.get("CF_KMS_KEY_ARN", "")
    if kms and not re.fullmatch(rf"arn:aws:kms:{region}:{account}:key/.+", kms):
        raise ValueError("CF_KMS_KEY_ARN must identify the secret's actual customer-managed key")
    registry = f"{account}.dkr.ecr.{region}.amazonaws.com"
    log_group = "/ecs/chunkforge"
    log_arn = f"arn:aws:logs:{region}:{account}:log-group:{log_group}:*"

    def logs(name: str) -> dict[str, Any]:
        return {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group,
                "awslogs-region": region,
                "awslogs-stream-prefix": name,
            },
        }

    env = {
        "CHUNKFORGE_ENVIRONMENT": "production",
        "CHUNKFORGE_LOG_LEVEL": "INFO",
        "CHUNKFORGE_REDIS_URL": "redis://127.0.0.1:6379/0",
        "CHUNKFORGE_OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        "CHUNKFORGE_OLLAMA_MODEL": "llama3.2:1b",
        "CHUNKFORGE_KAFKA_BOOTSTRAP_SERVERS": "127.0.0.1:9092",
        "CHUNKFORGE_EMBEDDING_MODEL": "BAAI/bge-small-en-v1.5",
        "CHUNKFORGE_RERANKER_MODEL": "cross-encoder/ms-marco-MiniLM-L6-v2",
        "CHUNKFORGE_RAW_DATA_DIR": "/tmp/raw",
        "CHUNKFORGE_PROCESSED_DATA_DIR": "/tmp/processed",
        "CHUNKFORGE_EMBEDDING_DEVICE": "cpu",
        "OMP_NUM_THREADS": "2",
        "TOKENIZERS_PARALLELISM": "false",
    }
    containers: list[dict[str, Any]] = [
        {
            "name": "api",
            "image": f"{registry}/chunkforge-api:{tag}",
            "essential": True,
            "memory": 6144,
            "cpu": 1024,
            "portMappings": [{"containerPort": 8000}],
            "environment": [{"name": k, "value": v} for k, v in env.items()],
            "secrets": [{"name": "CHUNKFORGE_POSTGRES_DSN", "valueFrom": secret}],
            "dependsOn": [
                {"containerName": "redis", "condition": "START"},
                {"containerName": "ollama", "condition": "HEALTHY"},
            ],
            "healthCheck": {
                "command": [
                    "CMD-SHELL",
                    'python -c "import urllib.request; '
                    "urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)\"",
                ],
                "interval": 30,
                "timeout": 5,
                "retries": 3,
                "startPeriod": 60,
            },
            "logConfiguration": logs("api"),
        },
        {
            "name": "frontend",
            "image": f"{registry}/chunkforge-ui:{tag}",
            "essential": True,
            "memory": 256,
            "cpu": 128,
            "portMappings": [{"containerPort": 80}],
            "dependsOn": [{"containerName": "api", "condition": "HEALTHY"}],
            "logConfiguration": logs("frontend"),
        },
        {
            "name": "redis",
            "image": "redis:7.4.1-alpine",
            "essential": True,
            "memory": 256,
            "cpu": 128,
            "command": [
                "redis-server",
                "--bind",
                "127.0.0.1",
                "--maxmemory",
                "128mb",
                "--maxmemory-policy",
                "allkeys-lru",
            ],
            "logConfiguration": logs("redis"),
        },
        {
            "name": "ollama",
            "image": "ollama/ollama:0.3.14",
            "essential": True,
            "memory": 6144,
            "cpu": 2048,
            "environment": [{"name": "OLLAMA_HOST", "value": "127.0.0.1:11434"}],
            "entryPoint": ["/bin/sh", "-c"],
            "command": [
                "ollama serve & server=$!; "
                "until ollama list >/dev/null 2>&1; do sleep 2; done; "
                "ollama pull llama3.2:1b || exit 1; wait $server"
            ],
            "healthCheck": {
                "command": ["CMD-SHELL", "ollama list | grep -q 'llama3.2:1b'"],
                "interval": 30,
                "timeout": 5,
                "retries": 10,
                "startPeriod": 300,
            },
            "logConfiguration": logs("ollama"),
        },
    ]
    task = {
        "family": "chunkforge",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "4096",
        "memory": "16384",
        "runtimePlatform": {"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
        "executionRoleArn": f"arn:aws:iam::{account}:role/chunkforge-execution",
        "taskRoleArn": f"arn:aws:iam::{account}:role/chunkforge-task",
        "ephemeralStorage": {"sizeInGiB": 40},
        "containerDefinitions": containers,
    }
    statements = [
        {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {
            "Effect": "Allow",
            "Action": [
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchCheckLayerAvailability",
            ],
            "Resource": [
                f"arn:aws:ecr:{region}:{account}:repository/chunkforge-api",
                f"arn:aws:ecr:{region}:{account}:repository/chunkforge-ui",
            ],
        },
        {
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": log_arn,
        },
        {"Effect": "Allow", "Action": "secretsmanager:GetSecretValue", "Resource": secret},
    ]
    if kms:
        statements.append({"Effect": "Allow", "Action": "kms:Decrypt", "Resource": kms})
    # Optional operator/ingestion role policy; do not attach to the API task by default.
    s3_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "s3:ListBucket",
                "Resource": f"arn:aws:s3:::{bucket}",
                "Condition": {"StringLike": {"s3:prefix": ["raw/*", "processed/*"]}},
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/raw/*", f"arn:aws:s3:::{bucket}/processed/*"],
            },
        ],
    }
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:ecs:{region}:{account}:*"},
                },
            }
        ],
    }
    output = Path(__file__).parent / "rendered"
    output.mkdir(exist_ok=True)
    for name, value in {
        "task-definition": task,
        "execution-policy": {"Version": "2012-10-17", "Statement": statements},
        "s3-ingestion-policy": s3_policy,
        "task-trust": trust,
    }.items():
        (output / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n")
    print(f"Rendered four documents under {output}; no AWS API calls were made.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
