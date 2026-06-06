#!/usr/bin/env bash
# Deploy RaktSetu to AWS App Runner (works when SAM/CloudFormation transform is blocked).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REGION="${AWS_REGION:-us-east-1}"
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REPO="raktsetu"
IMAGE="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest"
SERVICE="raktsetu-web"

echo "==> Ensuring DynamoDB tables..."
python3 scripts/create_dynamodb_tables.py --region "$REGION"

echo "==> Uploading secrets..."
python3 scripts/setup_secrets.py --region "$REGION"

echo "==> ECR repository..."
aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$REGION" >/dev/null

echo "==> Building Docker image..."
docker build -t "$REPO" .

echo "==> Pushing to ECR..."
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
docker tag "$REPO:latest" "$IMAGE"
docker push "$IMAGE"

# App Runner access role (pull from ECR)
ACCESS_ROLE="raktsetu-apprunner-access"
if ! aws iam get-role --role-name "$ACCESS_ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ACCESS_ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"build.apprunner.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  aws iam attach-role-policy --role-name "$ACCESS_ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
fi
ACCESS_ARN=$(aws iam get-role --role-name "$ACCESS_ROLE" --query Role.Arn --output text)

# Instance role (DynamoDB + Bedrock + Secrets Manager)
INSTANCE_ROLE="raktsetu-apprunner-instance"
if ! aws iam get-role --role-name "$INSTANCE_ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$INSTANCE_ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"tasks.apprunner.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  aws iam put-role-policy --role-name "$INSTANCE_ROLE" --policy-name raktsetu-runtime \
    --policy-document "$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["dynamodb:*"], "Resource": "arn:aws:dynamodb:${REGION}:${ACCOUNT}:table/raktsetu-*"},
    {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": "arn:aws:secretsmanager:${REGION}:${ACCOUNT}:secret:raktsetu/config-*"},
    {"Effect": "Allow", "Action": ["bedrock:InvokeModel"], "Resource": "*"}
  ]
}
EOF
)"
fi
INSTANCE_ARN=$(aws iam get-role --role-name "$INSTANCE_ROLE" --query Role.Arn --output text)

ENV_JSON=$(python3 <<'PY'
import json, os, sys
sys.path.insert(0, ".")
from shared import config
keys = [
    "SECRETS_MANAGER_SECRET_ID", "AWS_REGION",
    "DYNAMODB_TABLE_CONVERSATIONS", "DYNAMODB_TABLE_DONORS",
    "DYNAMODB_TABLE_PATIENTS", "DYNAMODB_TABLE_REQUESTS",
    "DYNAMODB_TABLE_APPOINTMENTS", "DYNAMODB_TABLE_WAITLIST",
    "DYNAMODB_TABLE_PROCESSED_MESSAGES", "LOCAL_MODE", "PORT",
    "AGENT_FORCE_BEDROCK",
]
env = [{"Name": "LOCAL_MODE", "Value": "0"}, {"Name": "PORT", "Value": "8080"}]
for k in keys:
    if k in ("LOCAL_MODE", "PORT"):
        continue
    v = config.get(k) or os.environ.get(k)
    if v:
        env.append({"Name": k, "Value": str(v)})
print(json.dumps(env))
PY
)

echo "==> Creating/updating App Runner service..."
EXISTING=$(aws apprunner list-services --region "$REGION" \
  --query "ServiceSummaryList[?ServiceName=='$SERVICE'].ServiceArn | [0]" --output text)

if [[ "$EXISTING" == "None" || -z "$EXISTING" ]]; then
  aws apprunner create-service --region "$REGION" --service-name "$SERVICE" \
    --source-configuration "ImageRepository={ImageIdentifier=$IMAGE,ImageRepositoryType=ECR,ImageConfiguration={Port=8080,RuntimeEnvironmentVariables=$ENV_JSON}},AuthenticationConfiguration={AccessRoleArn=$ACCESS_ARN}" \
    --instance-configuration "Cpu=1024,Memory=2048,InstanceRoleArn=$INSTANCE_ARN" \
    --health-check-configuration "Protocol=HTTP,Path=/health,Interval=20,Timeout=5,HealthyThreshold=1,UnhealthyThreshold=5" \
    --query 'Service.{Arn:ServiceArn,Status:Status}' --output json
else
  aws apprunner update-service --region "$REGION" --service-arn "$EXISTING" \
    --source-configuration "ImageRepository={ImageIdentifier=$IMAGE,ImageRepositoryType=ECR,ImageConfiguration={Port=8080,RuntimeEnvironmentVariables=$ENV_JSON}},AuthenticationConfiguration={AccessRoleArn=$ACCESS_ARN}" \
    --instance-configuration "Cpu=1024,Memory=2048,InstanceRoleArn=$INSTANCE_ARN" \
    --query 'Service.{Arn:ServiceArn,Status:Status}' --output json
fi

echo "Waiting for service to be running..."
for i in $(seq 1 30); do
  URL=$(aws apprunner list-services --region "$REGION" \
    --query "ServiceSummaryList[?ServiceName=='$SERVICE'].ServiceUrl | [0]" --output text)
  STATUS=$(aws apprunner list-services --region "$REGION" \
    --query "ServiceSummaryList[?ServiceName=='$SERVICE'].Status | [0]" --output text)
  echo "  status=$STATUS url=$URL"
  [[ "$STATUS" == "RUNNING" ]] && break
  sleep 15
done

BASE="https://${URL}"
echo ""
echo "Live URLs:"
echo "  Health:    $BASE/health"
echo "  WhatsApp:  $BASE/whatsapp"
echo "  Periskope: $BASE/periskope"
echo "  Vapi:      $BASE/vapi/tools"
echo ""
python3 scripts/setup_vapi.py --server-url "$BASE"
