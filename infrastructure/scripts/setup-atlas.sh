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

if [[ "${STAGE}" == "dev" ]]; then
  DEFAULT_ATLAS_TIER="M0"
else
  DEFAULT_ATLAS_TIER="FLEX"
fi
ATLAS_TIER="${ATLAS_TIER:-${DEFAULT_ATLAS_TIER}}"

PROJECT_NAME="portfolio-analyzer-${STAGE}"
CLUSTER_NAME="portfolio-analyzer-${STAGE}"
DB_USER="portfolioadmin"
DB_NAME="portfolio_analyzer"

# Atlas org IDs are 24-char hex strings. Catch display-name values early.
if [[ ! "${ATLAS_ORG_ID}" =~ ^[a-f0-9]{24}$ ]]; then
  echo "Error: ATLAS_ORG_ID must be the Atlas Org ID (24-char hex), not an org display name." >&2
  echo "Example: ATLAS_ORG_ID=67a069d621dbe1257d0d7578" >&2
  exit 1
fi

echo "=== MongoDB Atlas setup for stage: ${STAGE} ==="
echo "Tier: ${ATLAS_TIER}"
echo

# ── Atlas project ─────────────────────────────────────────────────────────────
echo "[1/5] Ensuring Atlas project exists: ${PROJECT_NAME}"
PROJECT_ID=$(atlas projects list \
  --orgId "${ATLAS_ORG_ID}" \
  --output json | jq -r --arg name "${PROJECT_NAME}" '.results[]? | select(.name == $name) | .id' | head -n1)

if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID=$(atlas projects create "${PROJECT_NAME}" \
    --orgId "${ATLAS_ORG_ID}" \
    --output json | jq -r '.id')
fi
echo "  Project ID: ${PROJECT_ID}"

# ── Cluster ───────────────────────────────────────────────────────────────────
echo "[2/5] Ensuring cluster exists: ${CLUSTER_NAME}"
if atlas clusters describe "${CLUSTER_NAME}" --projectId "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "  Cluster already exists; skipping create"
else
  if [[ "${ATLAS_TIER}" == "M0" ]]; then
    atlas clusters create "${CLUSTER_NAME}" \
      --projectId "${PROJECT_ID}" \
      --provider AWS \
      --region "${ATLAS_REGION}" \
      --tier "${ATLAS_TIER}"
  else
    atlas clusters create "${CLUSTER_NAME}" \
      --projectId "${PROJECT_ID}" \
      --provider AWS \
      --region "${ATLAS_REGION}" \
      --tier "${ATLAS_TIER}"
  fi
fi
echo "  Waiting for cluster to be ready (this takes ~3 minutes)…"
atlas clusters watch "${CLUSTER_NAME}" --projectId "${PROJECT_ID}"

# ── Database user ─────────────────────────────────────────────────────────────
echo "[3/5] Ensuring database user exists: ${DB_USER}"
DB_PASSWORD="$(openssl rand -base64 24)"
if atlas dbusers describe "${DB_USER}" --authDB admin --projectId "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "  Database user already exists; rotating password"
  atlas dbusers update "${DB_USER}" \
    --authDB admin \
    --projectId "${PROJECT_ID}" \
    --password "${DB_PASSWORD}" \
    --role "readWrite@${DB_NAME}"
else
  atlas dbusers create \
    --projectId "${PROJECT_ID}" \
    --username "${DB_USER}" \
    --password "${DB_PASSWORD}" \
    --role "readWrite@${DB_NAME}"
fi

# ── IP access list — allow all (Lambda has dynamic IPs) ──────────────────────
echo "[4/5] Opening IP access list to 0.0.0.0/0 (Lambda dynamic IPs)"
CIDR_EXISTS=$(atlas accessLists list \
  --projectId "${PROJECT_ID}" \
  --output json | jq -r '.results[]? | select(.cidrBlock == "0.0.0.0/0") | .cidrBlock' | head -n1)

if [[ -n "${CIDR_EXISTS}" ]]; then
  echo "  CIDR 0.0.0.0/0 already exists; skipping create"
else
  atlas accessLists create "0.0.0.0/0" \
    --type cidrBlock \
    --projectId "${PROJECT_ID}" \
    --comment "Lambda functions use dynamic IPs; security enforced via credentials + TLS"
fi

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
