# Phase 11 — Operator-led AWS deployment

## Status and scope

**Not deployed to AWS. No public application URL exists.** This kit renders real,
account-specific ECS/IAM documents; it never creates resources or reads credentials.
Cloud execution, cost approval and account/network choices belong to the operator.
Local validation cannot certify a successful deployment or cloud performance.

This v1 deploys one ECS Fargate task containing API, frontend, Redis and Ollama,
behind an Application Load Balancer (ALB). RDS PostgreSQL is durable vector storage;
S3 preserves raw/processed corpus backups. Kafka ingestion remains local in this
cloud scope; the API never calls Kafka on the synchronous query path. Don't claim
MSK, cloud streaming ingestion, or automatic S3 event handling has been deployed.

Why co-locate? Four containers share the task network, so API calls Redis/Ollama
on loopback and cloud Nginx proxies `127.0.0.1:8000`. This avoids inventing Docker
Compose DNS in ECS. It simplifies a portfolio deployment but couples scaling and
failure domains. Independent services with discovery/ALB routing are the next step.

## Teach the local → cloud changes

| Local                | Cloud choice                                    | Trade-off                                                      |
| -------------------- | ----------------------------------------------- | -------------------------------------------------------------- |
| Docker Compose       | ECS Fargate                                     | Managed task lifecycle; no Kubernetes node operations          |
| PostgreSQL volume    | Private RDS PostgreSQL 16 with vector extension | Managed backups; ongoing cost and connection/network planning  |
| `.env` DSN           | Secrets Manager injection                       | Execution role retrieves it; rotation requires task refresh    |
| Local corpus files   | Private, encrypted/versioned S3 prefixes        | Durable object backup; no automatic app S3 integration claimed |
| Docker logs          | CloudWatch log streams                          | Central inspection; retention must be bounded                  |
| Host published ports | ALB → task security group                       | Only frontend exposed; DB/cache/model remain private           |
| Mac ARM64 images     | Explicit Linux AMD64 builds                     | Fargate task architecture and image architecture must agree    |

Execution role: ECS pulls images, injects secrets and writes logs. Task role:
application AWS permissions. The query API doesn't call S3, so its task role has
no S3 policy by default. An optional narrowly scoped S3 ingestion policy is rendered
for a separate operator/ingestion identity. RDS network access isn't granted by an
S3 policy; database credentials and security groups govern this setup.

## 1. Prerequisites and budget gate

Install AWS CLI v2, Docker buildx, and PostgreSQL 16 client tools. Authenticate with
your own AWS SSO profile; verify `aws sts get-caller-identity`. Never paste keys into
this repository. Use a sandbox account and set a billing alarm before provisioning.
This CPU-model task reserves 4 vCPU/16 GiB, plus RDS, ALB, logs, storage and possibly
NAT. It is not a free-tier promise; inspect the AWS pricing calculator for your
region and chosen resources. Start desired count at one and tear down after demos.

Record your region, VPC, private subnet IDs in two AZs, ALB public subnet IDs,
task/ALB/RDS security groups, RDS endpoint, DSN secret ARN and image tag in an
untracked `aws/deploy.env` or operator notes. Do not invent resource IDs.

## 2. Provision network and database (AWS console, operator executes)

1. Use a dedicated VPC or an approved existing one. Create private task/RDS
   subnets in two AZs and public ALB subnets. RDS must not be publicly accessible.
2. ALB security group accepts HTTPS from your intended audience. During the
   unauthenticated demo, restrict to your own IP. No public general-use release.
3. Task SG accepts port 80 **only from ALB SG**. Do not allow inbound 8000, 6379
   or 11434. RDS SG accepts 5432 **only from task SG** and an approved migration
   environment's SG. Enable outbound access needed for models/images/logs.
4. Private tasks need NAT for Docker Hub/model downloads in this version. ECR,
   CloudWatch, Secrets Manager and S3 endpoints alone won't reach model registries.
   Verify routing/DNS; NAT costs money. A hardened image/model mirror can remove
   this dependency later.
5. Create encrypted RDS PostgreSQL 16 in a private DB subnet group, with backups
   and deletion protection appropriate to the demo. Verify the selected minor
   version supports the desired `vector` extension; do not assume every version
   exposes identical extension versions.
6. In an authorized in-VPC SQL client (for example a VPC CloudShell environment
   or your approved SSM tunnel), connect with the migration/admin role over TLS:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

Create database `chunkforge` and a dedicated login. Use a separate runtime role
with CONNECT, USAGE on schema public and SELECT on the two corpus tables; the
query API doesn't need table creation or destructive DDL. The migration/ingestion
role needs the appropriate write/schema rights. Set secure passwords through your
approved secret workflow, not tracked SQL. Store the **complete DSN string** as a
Secrets Manager plaintext secret, including URL-encoded credentials and
`?sslmode=require`. A JSON object containing `password` is not this renderer's
expected secret format. For certificate identity verification, configure the RDS
CA bundle and `sslmode=verify-full`; `require` alone isn't hostname verification.

## 3. Transfer the verified corpus without recomputing embeddings

Create a private S3 bucket with Block Public Access, encryption and versioning.
Use prefixes `raw/` and `processed/`. From the local repository:

```sh
aws s3 sync data/raw/ "s3://$CF_BUCKET/raw/"
aws s3 sync data/processed/ "s3://$CF_BUCKET/processed/"
docker compose exec -T postgres pg_dump -U chunkforge -d chunkforge \
  --format=custom --no-owner --no-acl > /private/tmp/chunkforge-corpus.dump
```

Treat the dump/corpus as data, not public Git assets. Upload the dump to an
operator-private S3 backup prefix using your existing operator identity (the
rendered application policy intentionally doesn't grant that prefix). Restore
from an in-VPC migration environment with network reachability and PostgreSQL16
`pg_restore`, specifying the RDS connection through your approved secure
credential mechanism. Do not put the DSN in shell history. Restore only to the
dedicated empty database; inspect the archive with `pg_restore --list` first.

```sh
pg_restore --no-owner --no-acl --dbname=chunkforge chunkforge-corpus.dump
```

Here host/user/TLS/password come from that migration environment's secure libpq
configuration. Verify counts and grant runtime SELECT permissions after restore:

```sql
SELECT strategy, count(*) AS documents, sum(chunk_count) AS chunks
FROM chunkforge_chunks_runs GROUP BY strategy ORDER BY strategy;
SELECT indexname FROM pg_indexes WHERE tablename='chunkforge_chunks';
```

Expect 75 completed documents per strategy. Don't restore into your local corpus
or run clean/drop flags against an existing database.

## 4. Set actual account inputs and render

Run from the repo root in a shell whose AWS profile/region you have selected:

```sh
export CF_ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
export CF_REGION="$(aws configure get region)"
export CF_TAG="$(git rev-parse --short HEAD)"
printf 'Private corpus bucket name: '
read -r CF_BUCKET
export CF_BUCKET
printf 'Complete DSN secret ARN: '
read -r CF_DSN_SECRET_ARN
export CF_DSN_SECRET_ARN
python3 aws/render.py
```

If using a customer-managed KMS key for the DSN secret, export its real
`CF_KMS_KEY_ARN` before rendering and ensure the key policy permits the execution
role. Rendered documents are ignored by Git. Review each before applying.

```sh
aws iam create-role --role-name chunkforge-execution \
  --assume-role-policy-document file://aws/rendered/task-trust.json
aws iam put-role-policy --role-name chunkforge-execution --policy-name chunkforge-execution \
  --policy-document file://aws/rendered/execution-policy.json
aws iam create-role --role-name chunkforge-task \
  --assume-role-policy-document file://aws/rendered/task-trust.json
aws logs create-log-group --log-group-name /ecs/chunkforge
aws logs put-retention-policy --log-group-name /ecs/chunkforge --retention-in-days 7
```

Existing resources should be inspected/reused instead of blindly rerunning create
commands. The deploying identity also needs narrowly scoped `iam:PassRole` for
these two roles; that permission is not granted to application containers.

## 5. Build and push artifacts

```sh
aws ecr create-repository --repository-name chunkforge-api --image-scanning-configuration scanOnPush=true
aws ecr create-repository --repository-name chunkforge-ui --image-scanning-configuration scanOnPush=true
export CF_REGISTRY="$CF_ACCOUNT.dkr.ecr.$CF_REGION.amazonaws.com"
aws ecr get-login-password --region "$CF_REGION" | \
  docker login --username AWS --password-stdin "$CF_REGISTRY"
docker buildx build --platform linux/amd64 -f Dockerfile.api \
  -t "$CF_REGISTRY/chunkforge-api:$CF_TAG" --push .
docker buildx build --platform linux/amd64 --build-arg NGINX_CONFIG=nginx.ecs.conf \
  -t "$CF_REGISTRY/chunkforge-ui:$CF_TAG" --push frontend
aws ecs register-task-definition --cli-input-json file://aws/rendered/task-definition.json
```

Use immutable image tags/digests for a real release. The cloud Nginx config is
different deliberately: co-task containers share loopback, not Compose DNS.
Ollama weights and Redis are ephemeral in this first cloud task. Redis is only a
reconstructable cache; models are pulled on startup, causing cold-start delay and
external-registry dependency. No durable model cache/HA claim. EFS or baked model
artifacts require a separate measured storage/security decision.

## 6. Create ALB and ECS service (console)

1. Create an ECS cluster named `chunkforge`. Create an ALB with an IP target group
   on HTTP80, target health path `/api/health`, and an ACM-backed HTTPS listener.
2. Create an ECS service from the registered task revision: Fargate Linux1.4+
   platform, one task, selected private subnets/task SG, no public IP. Attach the
   target group to container `frontend`, port 80. Set health-check grace period
   900 seconds for first model download; it isn't a readiness guarantee.
3. The task definition allocates 4 vCPU/16 GiB; model memory sizing is an initial
   deployment hypothesis, not an AWS benchmark. Inspect RSS/CPU and adjust after
   a real `/ask`. Configure deployment rollback/circuit breaker and alarms.
4. With no custom domain, ALB HTTP can be used only for a temporary IP-restricted
   smoke test; do not present unencrypted unauthenticated public access as production.

## 7. Verify, then record the real URL

```sh
aws ecs describe-services --cluster chunkforge --services chunkforge
aws logs tail /ecs/chunkforge --since 10m
```

At the actual HTTPS endpoint: load UI, call `/api/health`, `/api/search` and
`/api/ask`, compare source/citation IDs, inspect degradation flags, and confirm
model/DB logs contain no credentials. Verify reboot recovery, task replacement,
RDS backup policy and the selected corpus counts. Record service/task revision,
image digests, timestamp and real endpoint in `docs/OPERATIONS.md` only after
successful execution. A green liveness target does not prove model readiness.

## Debug / rollback / teardown

| Failure                     | Check                                                              |
| --------------------------- | ------------------------------------------------------------------ |
| CannotPullContainerError    | ECR tag/architecture, execution role, NAT/endpoints                |
| ResourceInitializationError | Secret ARN/KMS, log group, network routing                         |
| DB timeout                  | Task→RDS SG5432, DNS, private routes, TLS/user/database            |
| Nginx502                    | API process health; built `nginx.ecs.conf`, not Compose upstream   |
| Ask evidence-only           | Ollama download/memory/timeout; inspect explicit reasons           |
| Worker can't see PDF        | Cloud Kafka worker isn't deployed; S3 isn't mounted as a directory |

Rollback by selecting the previous **known-good task revision/image digest**, then
force a new service deployment; don't delete RDS data. Scale ECS desired count to
zero after the demo. ALB, NAT, RDS and retained storage may still incur charges.
Delete only named demo resources after reviewing dependencies/backups; retain a
final DB snapshot if needed, then remove service/ALB/NAT/endpoints/logs/ECR/S3/RDS
through reviewed console actions. Never use broad account-wide deletion commands.

## Phase 11 interview checkpoint

1. **ECS vs EKS?** Fargate minimizes cluster operations for this scope; EKS adds
   Kubernetes ecosystem/flexibility plus real administration overhead.
2. **Execution vs task role?** Agent startup permissions versus application API
   permissions; query-only code should not receive unused corpus-write authority.
3. **Why not deploy Compose unchanged?** DNS, network identity, secrets, durable
   storage and resource lifecycle differ. The cloud Nginx loopback config proves it.
4. **What is durable?** RDS/S3 according to their configured backups/versioning;
   task-local Redis/model files are reconstructable and ephemeral.
5. **What breaks at scale?** Coupled model/API scaling, per-task BM25/model memory,
   cold starts and DB capacity; separate serving and indexing after measurements.

## Official implementation references

Checked against [ECS execution roles](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_execution_IAM_role.html),
[awsvpc networking](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-networking-awsvpc.html),
and [RDS pgvector support](https://aws.amazon.com/about-aws/whats-new/2024/11/amazon-rds-for-postgresql-pgvector-080/).
These references support service mechanics, not a claim this repository is deployed.
