#!/usr/bin/env bash
# Retry all queue-full rescan failures until none remain.
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate

INPUT="${1:-rescan_report_20260701_203516.csv}"
LOG="rescan_until_done.log"
OUTPUT="rescan_final_report.csv"

while true; do
  echo "$(date -Iseconds) Starting rescan batch..." | tee -a "$LOG"
  python -u rescan_manual.py \
    -i "$INPUT" \
    --status failed \
    --queue-only \
    --until-done \
    --workers 2 \
    --skip-active-check \
    --round-sleep 120 \
    --resume-log "$LOG" \
    -o "$OUTPUT" 2>&1 | tee -a "$LOG" || true

  remaining=$(python - <<'PY'
import csv, re
from pathlib import Path

log = Path("rescan_until_done.log")
done = set()
if log.exists():
    pat = re.compile(r" INFO Rescanned (.+?) scan=")
    for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = pat.search(line)
        if m:
            done.add(m.group(1))

pending = 0
with open("rescan_report_20260701_203516.csv", newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if row.get("status") != "failed":
            continue
        reason = row.get("reason") or ""
        if "Max Queued" not in reason:
            continue
        if row.get("project_name", "") not in done:
            pending += 1
print(pending)
PY
)

  echo "$(date -Iseconds) Queue-full remaining: $remaining" | tee -a "$LOG"
  if [ "$remaining" -eq 0 ]; then
    echo "$(date -Iseconds) All queue-full rescans submitted." | tee -a "$LOG"
    break
  fi
  echo "$(date -Iseconds) Waiting 120s before next attempt..." | tee -a "$LOG"
  sleep 120
done
