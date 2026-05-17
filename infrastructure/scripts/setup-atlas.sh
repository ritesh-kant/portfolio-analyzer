#!/usr/bin/env bash
# Provisions a MongoDB Atlas project, cluster, database user, and IP access list
# using the Atlas CLI (https://www.mongodb.com/docs/atlas/cli/stable/).
#
# Usage:
#   ATLAS_ORG_ID=<org-id> STAGE=dev bash setup-atlas.sh
#
# Prerequisites:
#   - Atlas CLI installed: brew install mongodb-atlas-cli
#   - Logged in: atlas auth login
#   - ATLAS_ORG_ID environment variable set (Organisation > Settings > Org ID)

set -euo pipefail

STAGE="${STAGE:-dev}"
ATLAS_ORG_ID="${ATLAS_ORG_ID:?Set ATLAS_ORG_ID to your MongoDB Atlas Organisation ID}"
ATLAS_REGION="${ATLAS_REGION:-AP_SOUTH_1}"
PROJECT_NAME="portfolio-analyzer-${STAGE}"
CLUSTER_NAME="portfolio-analyzer-${STAGE}"
DB_USER="portfolioadmin"
DB_NAME="portfolio_analyzer"

echo "=== MongoDB Atlas setup for stage: ${STAGE} ==="
echo

# ── Atlas project ─────────────────────────────────────────────────────────────
echo "[1/5] Creating Atlas project: ${PROJECT_NAME}"
PROJECT_ID=$(atlas projects create "${PROJECT_NAME}" \
  --orgId "${ATLAS_ORG_ID}" \
  --output json | jq -r '.id')
echo "  Project ID: ${PROJECT_ID}"

# ── Cluster ───────────────────────────────────────────────────────────────────
echo "[2/5] Creating cluster: ${CLUSTER_NAME}"
if [[ "${STAGE}" == "dev" ]]; then
  # M0 free tier for dev
  atlas clusters create "${CLUSTER_NAME}" \
    --projectId "${PROJECT_ID}" \
    --provider AWS \
    --region "${ATLAS_REGION}" \
    --tier M0 \
    --username "${DB_USER}" \
    --password "$(openssl rand -base64 24)" \
    --no-cli-pager
else
  # Serverless (pay-per-operation) for staging/prod
  atlas serverless create "${CLUSTER_NAME}" \
    --projectId "${PROJECT_ID}" \
    --provider AWS \
    --no-cli-pager
fi
echo "  Waiting for cluster to be ready (this takes ~3 minutes)…"
atlas clusters watch "${CLUSTER_NAME}" --projectId "${PROJECT_ID}"

# ── Database user ─────────────────────────────────────────────────────────────
echo "[3/5] Creating database user: ${DB_USER}"
DB_PASSWORD="$(openssl rand -base64 24)"
atlas dbusers create \
  --projectId "${PROJECT_ID}" \
  --username "${DB_USER}" \
  --password "${DB_PASSWORD}" \
  --role "readWrite@${DB_NAME}" \
  --no-cli-pager

# ── IP access list — allow all (Lambda has dynamic IPs) ──────────────────────
echo "[4/5] Opening IP access list to 0.0.0.0/0 (Lambda dynamic IPs)"
atlas accessLists create \
  --projectId "${PROJECT_ID}" \
  --cidr "0.0.0.0/0" \
  --comment "Lambda functions use dynamic IPs; security enforced via credentials + TLS" \
  --no-cli-pager

# ── Build connection string ───────────────────────────────────────────────────
echo "[5/5] Fetching connection string"
CLUSTER_HOST=$(atlas clusters connectionStrings describe "${CLUSTER_NAME}" \
  --projectId "${PROJECT_ID}" \
  --output json | jq -r '.standardSrv' | sed 's|mongodb+srv://||')

CONNECTION_STRING="mongodb+srv://${DB_USER}:${DB_PASSWORD}@${CLUSTER_HOST}/${DB_NAME}?retryWrites=true&w=majority"

echo
echo "=== Atlas setup complete ==="
echo
echo "Connection string (add to SSM via setup-ssm.sh):"
echo "  ${CONNECTION_STRING}"
echo
echo "Next step: run setup-ssm.sh and paste the connection string when prompted."
