#!/usr/bin/env bash
# Deploy RaktSetu to AWS (Lambda + DynamoDB + Step Functions + API Gateway).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REGION="${AWS_REGION:-us-east-1}"
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"

echo "==> Region: $REGION"

if ! command -v sam >/dev/null 2>&1; then
  echo "ERROR: AWS SAM CLI not installed."
  echo "  Install: brew install aws-sam-cli"
  echo "     or:   pip3 install aws-sam-cli"
  exit 1
fi

echo "==> Uploading secrets to Secrets Manager..."
python3 scripts/setup_secrets.py --region "$REGION"

echo "==> Building SAM application..."
sam build -t infra/template.yaml

echo "==> Deploying stack (raktsetu)..."
sam deploy --config-file samconfig.toml --region "$REGION"

echo ""
echo "==> Stack outputs:"
aws cloudformation describe-stacks --stack-name raktsetu --region "$REGION" \
  --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' --output table

VAPI_URL=$(aws cloudformation describe-stacks --stack-name raktsetu --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='VapiToolsUrl'].OutputValue" --output text)

if [[ -n "$VAPI_URL" && "$VAPI_URL" != "None" ]]; then
  BASE="${VAPI_URL%/vapi/tools}"
  echo ""
  echo "==> Pointing Vapi assistant at $BASE ..."
  python3 scripts/setup_vapi.py --server-url "$BASE"
fi

echo ""
echo "Done. Next steps:"
echo "  1. Periskope dashboard → Webhooks → set message.created URL to PeriskopeWebhookUrl above"
echo "  2. Twilio (if used) → WhatsApp sender webhook → WhatsappWebhookUrl above"
echo "  3. Message +919372875356 on WhatsApp to test (Periskope)"
echo "  4. Call your Vapi phone number to test voice"
