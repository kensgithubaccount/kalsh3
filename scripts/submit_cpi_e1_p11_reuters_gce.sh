#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
INPUT="${2:-}"

INSTANCE="${CPI_P11_GCP_INSTANCE:-kalsh3-cpi-p11-20261014}"
ZONE="${CPI_P11_GCP_ZONE:-us-east4-b}"
PROJECT="${CPI_P11_GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"

if [ "$MODE" != "pass" ] && [ "$MODE" != "nonpass" ]; then
  echo "Usage: $0 pass|nonpass /path/to/reuters-receipt.json"
  exit 2
fi

if [ -z "$INPUT" ] || [ ! -f "$INPUT" ]; then
  echo "BLOCKER: receipt JSON file is required"
  exit 2
fi

if [ -z "$PROJECT" ] || [ "$PROJECT" = "(unset)" ]; then
  echo "BLOCKER: no Google Cloud project is configured"
  exit 2
fi

REMOTE="/tmp/cpi_p11_reuters_${MODE}_$(date -u +%Y%m%dT%H%M%SZ).json"

gcloud compute scp "$INPUT" "$INSTANCE:$REMOTE"   --project="$PROJECT"   --zone="$ZONE"   --quiet

COMMAND="record-reuters-$MODE"

gcloud compute ssh "$INSTANCE"   --project="$PROJECT"   --zone="$ZONE"   --command="~/run_cpi_p11_frozen_vm.sh '$COMMAND' '$REMOTE'"   --quiet
