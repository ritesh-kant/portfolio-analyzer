#!/usr/bin/env bash
# Creates all SSM SecureString parameters that portfolio-analyzer services read.
# CloudFormation cannot create SecureString parameters, so this script fills the gap.
#
# Usage:
#   STAGE=dev bash setup-ssm.sh
#   STAGE=prod bash setup-ssm.sh
#
# Prerequisites:
#   - AWS CLI configured with credentials that have ssm:PutParameter permission
#   - MongoDB Atlas cluster provisioned (run setup-atlas.sh first)
#   - All required values available

set -euo pipefail

STAGE="${STAGE:-dev}"
REGION="${AWS_REGION:-ap-south-1}"
PREFIX="/portfolio-analyzer/${STAGE}"

put_param() {
  local name="$1"
  local value="$2"
  local desc="$3"
  echo "  → ${PREFIX}/${name}"
  aws ssm put-parameter \
    --region "${REGION}" \
    --name "${PREFIX}/${name}" \
    --value "${value}" \
    --type SecureString \
    --description "${desc}" \
    --overwrite \
    --no-cli-pager
}

echo "=== SSM parameter setup for stage: ${STAGE} ==="
echo

# ── MongoDB Atlas ─────────────────────────────────────────────────────────────
# Get the connection string from Atlas after running setup-atlas.sh.
# Format: mongodb+srv://<user>:<pass>@<cluster-host>/portfolio_analyzer?retryWrites=true&w=majority
read -r -p "MongoDB Atlas connection string (mongodb+srv://...): " MONGODB_URI
put_param "MONGODB_URI" "${MONGODB_URI}" "MongoDB Atlas connection string"

# ── Signal-engine API key ─────────────────────────────────────────────────────
# Used to authenticate requests between trading-service and signal-engine.
read -r -p "Signal-engine API key (generate a random secret): " SIGNAL_ENGINE_API_KEY
put_param "SIGNAL_ENGINE_API_KEY" "${SIGNAL_ENGINE_API_KEY}" "API key for signal-engine authentication"

# ── AI provider keys (optional — only fill the one you use) ──────────────────
read -r -p "Anthropic API key (leave blank to skip): " ANTHROPIC_API_KEY
if [[ -n "${ANTHROPIC_API_KEY}" ]]; then
  put_param "ANTHROPIC_API_KEY" "${ANTHROPIC_API_KEY}" "Anthropic Claude API key"
fi

read -r -p "OpenAI API key (leave blank to skip): " OPENAI_API_KEY
if [[ -n "${OPENAI_API_KEY}" ]]; then
  put_param "OPENAI_API_KEY" "${OPENAI_API_KEY}" "OpenAI API key"
fi

read -r -p "Gemini API key (leave blank to skip): " GEMINI_API_KEY
if [[ -n "${GEMINI_API_KEY}" ]]; then
  put_param "GEMINI_API_KEY" "${GEMINI_API_KEY}" "Google Gemini API key"
fi

echo
echo "=== Done. Parameters written under ${PREFIX}/ ==="
echo "Next: deploy infrastructure/serverless.yml, then application services."
