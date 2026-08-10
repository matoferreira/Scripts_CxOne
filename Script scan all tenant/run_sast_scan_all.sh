#!/usr/bin/env bash
# Launch SAST-only scans for all tenant projects (SCM + rescan, no re-upload).
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate

LOG="sast_scan_all.log"
OUTPUT="sast_scan_report.csv"

echo "$(date -Iseconds) Starting SAST-only bulk scan..." | tee "$LOG"
python -u scan_all_projects.py \
  --engines sast \
  --workers 5 \
  --skip-active-check \
  --output "$OUTPUT" \
  --manual-output sast_manual_review.csv \
  2>&1 | tee -a "$LOG"

echo "$(date -Iseconds) Done." | tee -a "$LOG"
