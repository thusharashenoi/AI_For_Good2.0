#!/usr/bin/env bash
# Deploy Bridge Intelligence layer to AWS (SAM + pre-built Lambda bundle).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then set -a; source .env; set +a; fi
export STAGE="${STAGE:-dev}"

SAM="${ROOT}/.venv/bin/sam"
if [[ ! -x "$SAM" ]]; then
  echo "Installing aws-sam-cli into .venv..."
  .venv/bin/pip install -q aws-sam-cli
fi

echo "==> Ensuring DynamoDB tables exist (stage=$STAGE)..."
.venv/bin/python -m scripts.create_tables

echo "==> Seeding DynamoDB..."
.venv/bin/python -u -m scripts.seed_dynamodb

echo "==> Building Lambda bundle (manylinux x86_64 deps)..."
BUNDLE="${ROOT}/lambda_bundle"
rm -rf "$BUNDLE" "${ROOT}/.lambda_pkg"
mkdir -p "${ROOT}/.lambda_pkg"
.venv/bin/pip install -q -r requirements-lambda.txt -t "${ROOT}/.lambda_pkg" \
  --platform manylinux2014_x86_64 --python-version 3.10 --only-binary=:all: --upgrade
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE"
cp -r "${ROOT}/.lambda_pkg/"* "$BUNDLE/"
cp -r raktsetu lambdas engagement "$BUNDLE/"
# Strip tests/cache so unzipped bundle stays under Lambda's 250 MB limit.
find "$BUNDLE" -type d \( -name tests -o -name __pycache__ -o -name test -o -name testing \) -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUNDLE" -name '*.pyc' -delete 2>/dev/null || true
echo "  bundle size: $(du -sh "$BUNDLE" | cut -f1)"

echo "==> Packaging Lambda zip..."
ZIP="${ROOT}/.lambda_deploy.zip"
rm -f "$ZIP"
(cd "$BUNDLE" && zip -r9q "$ZIP" .)

DEPLOY_BUCKET="${LAMBDA_DEPLOY_BUCKET:-aws-sam-cli-managed-default-samclisourcebucket-o4wg9otzf4bg}"
LAMBDA_KEY="raktsetu-intelligence/lambda-${STAGE}-$(date +%Y%m%d%H%M%S).zip"
echo "==> Uploading Lambda package to s3://${DEPLOY_BUCKET}/${LAMBDA_KEY}..."
aws s3 cp "$ZIP" "s3://${DEPLOY_BUCKET}/${LAMBDA_KEY}"

echo "==> CloudFormation deploy (plain template, no SAM transform)..."
aws cloudformation deploy \
  --template-file "${ROOT}/infra/template-plain.yaml" \
  --stack-name "raktsetu-intelligence-${STAGE}" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    Stage="${STAGE}" \
    PlannerMobilize=0 \
    LambdaS3Bucket="${DEPLOY_BUCKET}" \
    LambdaS3Key="${LAMBDA_KEY}" \
  --no-fail-on-empty-changeset

API_URL=$(aws cloudformation describe-stacks --stack-name "raktsetu-intelligence-${STAGE}" \
  --query "Stacks[0].Outputs[?OutputKey=='AdminApiUrl'].OutputValue" --output text 2>/dev/null || true)
UI_BUCKET=$(aws cloudformation describe-stacks --stack-name "raktsetu-intelligence-${STAGE}" \
  --query "Stacks[0].Outputs[?OutputKey=='AdminUiBucketName'].OutputValue" --output text 2>/dev/null || true)
UI_URL=$(aws cloudformation describe-stacks --stack-name "raktsetu-intelligence-${STAGE}" \
  --query "Stacks[0].Outputs[?OutputKey=='AdminUiUrl'].OutputValue" --output text 2>/dev/null || true)

if [[ -n "$API_URL" && "$API_URL" != "None" ]]; then
  echo "==> Building admin UI (VITE_API_BASE=$API_URL)..."
  cd "${ROOT}/admin"
  if [[ ! -d node_modules ]]; then npm install --silent; fi
  VITE_API_BASE="$API_URL" npm run build
  if [[ -n "$UI_BUCKET" && "$UI_BUCKET" != "None" ]]; then
    echo "==> Uploading admin UI to s3://${UI_BUCKET}..."
    aws s3 sync dist/ "s3://${UI_BUCKET}/" --delete
  fi
fi

echo ""
echo "Deployed."
echo "  Admin API: ${API_URL}"
echo "  Admin UI:  ${UI_URL}"
echo ""
echo "Smoke test:"
echo "  curl ${API_URL}/healthz"
echo "  curl ${API_URL}/stats"
