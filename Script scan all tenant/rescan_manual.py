#!/usr/bin/env python3
"""Rescan non-SCM projects using POST /api/scans/rescan (no re-upload).

Reads project IDs from a manual_review CSV (default: latest manual_review_*.csv).
"""
import argparse
import csv
import logging
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from glob import glob
from pathlib import Path

from dotenv import load_dotenv

from cxone.client import CxOneClient, load_settings
from cxone.report import ProjectOutcome, default_report_path, write_manual_review_report, write_report
from cxone.scanner import ScanOrchestrator


def find_latest_manual_review_csv() -> Path:
    files = sorted(glob("manual_review_*.csv"), reverse=True)
    if not files:
        raise FileNotFoundError("No manual_review_*.csv found in current directory")
    return Path(files[0])


def load_manual_review_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def rescanned_names_from_log(log_path: Path) -> set[str]:
    """Parse 'Rescanned <name> scan=...' lines from a prior run log."""
    names: set[str] = set()
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2} .* INFO Rescanned (.+?) scan=")
    with log_path.open(encoding="utf-8") as f:
        for line in f:
            match = pattern.search(line)
            if match:
                names.add(match.group(1))
    return names


def is_queue_full_reason(reason: str | None) -> bool:
    return bool(reason and "Max Queued" in reason)


def filter_rows(
    rows: list[dict[str, str]],
    *,
    status: str,
    queue_only: bool,
) -> list[dict[str, str]]:
    if status != "all":
        rows = [r for r in rows if r.get("status") == status]
    if queue_only:
        rows = [r for r in rows if is_queue_full_reason(r.get("reason"))]
    return rows


def run_rescan_batch(
    orchestrator: ScanOrchestrator,
    rows: list[dict[str, str]],
) -> list[ProjectOutcome]:
    orchestrator.prefetch_last_scans([r["project_id"] for r in rows])
    outcomes: list[ProjectOutcome] = []
    with ThreadPoolExecutor(max_workers=orchestrator.submit_workers) as pool:
        futures = {
            pool.submit(
                orchestrator.rescan_project_by_id,
                row["project_id"],
                row.get("project_name", ""),
            ): row
            for row in rows
        }
        done = 0
        total = len(rows)
        for fut in as_completed(futures):
            try:
                result = fut.result()
            except Exception as exc:
                row = futures[fut]
                logging.error("Unhandled error for %s: %s", row.get("project_name"), exc)
                outcomes.append(
                    ProjectOutcome(
                        project_id=row["project_id"],
                        project_name=row.get("project_name", ""),
                        origin="",
                        main_branch="",
                        repo_id="",
                        status="failed",
                        scan_id=None,
                        reason=str(exc)[:500],
                    )
                )
                done += 1
                continue
            outcomes.append(result.to_outcome())
            done += 1
            if result.success:
                logging.info(
                    "Rescanned %s scan=%s [%s/%s]",
                    result.project_name,
                    result.scan_id,
                    done,
                    total,
                )
            elif result.status != "skipped_active":
                logging.warning(
                    "Failed %s: %s [%s/%s]",
                    result.project_name,
                    result.error,
                    done,
                    total,
                )
    return outcomes


def rows_from_outcomes(outcomes: list[ProjectOutcome]) -> list[dict[str, str]]:
    return [
        {
            "project_id": o.project_id,
            "project_name": o.project_name,
            "origin": o.origin,
            "main_branch": o.main_branch,
            "repo_id": o.repo_id,
            "status": o.status,
            "scan_id": o.scan_id or "",
            "reason": o.reason or "",
        }
        for o in outcomes
    ]


def merge_outcomes(
    existing: dict[str, ProjectOutcome],
    batch: list[ProjectOutcome],
) -> None:
    for outcome in batch:
        prev = existing.get(outcome.project_id)
        if prev and prev.status == "rescanned" and outcome.status == "failed":
            if is_queue_full_reason(outcome.reason):
                continue
        existing[outcome.project_id] = outcome


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Rescan projects via POST /api/scans/rescan (no source re-upload)"
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=None,
        help="manual_review CSV (default: latest manual_review_*.csv)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Rescan report CSV path",
    )
    parser.add_argument(
        "--manual-output",
        type=Path,
        default=None,
        help="CSV for projects still needing manual testing",
    )
    parser.add_argument("-n", "--limit", type=int, default=None, help="Max projects to rescan")
    parser.add_argument(
        "--resume-log",
        type=Path,
        default=None,
        help="Skip projects already logged as rescanned in a prior run log",
    )
    parser.add_argument(
        "--status",
        choices=("failed", "manual_review", "all"),
        default="all",
        help="When input is a rescan/manual report CSV, filter by status (default: all)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel rescan workers (default: CX_SUBMIT_WORKERS from env)",
    )
    parser.add_argument(
        "--wait-for-capacity",
        action="store_true",
        help="Pause submissions while active scans >= CX_MAX_CONCURRENT",
    )
    parser.add_argument(
        "--engines",
        default="sast",
        help="Comma-separated engines for rescan (default: sast)",
    )
    parser.add_argument(
        "--queue-only",
        action="store_true",
        help="Only retry failures caused by Max Queued (code 142)",
    )
    parser.add_argument(
        "--until-done",
        action="store_true",
        help="Keep retrying queue-full failures until all succeed or non-recoverable",
    )
    parser.add_argument(
        "--round-sleep",
        type=int,
        default=60,
        help="Seconds to wait between retry rounds when using --until-done (default: 60)",
    )
    parser.add_argument(
        "--skip-active-check",
        action="store_true",
        help="Do not skip projects with running/queued scans (fewer API calls)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    input_path = args.input or find_latest_manual_review_csv()
    rows = load_manual_review_rows(input_path)
    rows = filter_rows(rows, status=args.status, queue_only=args.queue_only)
    if args.status != "all" or args.queue_only:
        logging.info(
            "Filtered to %s rows (status=%s, queue_only=%s)",
            len(rows),
            args.status,
            args.queue_only,
        )
    if args.resume_log:
        done_names = rescanned_names_from_log(args.resume_log)
        before = len(rows)
        rows = [r for r in rows if r.get("project_name", "") not in done_names]
        logging.info(
            "Resume: skipping %s already rescanned (from %s), %s remaining",
            before - len(rows),
            args.resume_log,
            len(rows),
        )
    if args.limit:
        rows = rows[: args.limit]

    settings = load_settings()
    from cxone.client import _parse_scan_engines

    settings.scan_engines = _parse_scan_engines(args.engines)
    submit_workers = args.workers if args.workers is not None else settings.submit_workers
    client = CxOneClient(settings)
    orchestrator = ScanOrchestrator(
        client=client,
        max_concurrent=settings.max_concurrent,
        submit_workers=submit_workers,
        scan_tag=settings.scan_tag,
        scan_engines=settings.scan_engines,
        skip_active=not args.skip_active_check,
        wait_for_capacity=args.wait_for_capacity,
    )

    logging.info("Region: %s | Rescanning %s projects from %s", settings.api_host, len(rows), input_path)

    checkpoint = Path(args.output or "rescan_until_done_checkpoint.csv")
    all_outcomes: dict[str, ProjectOutcome] = {}

    if args.until_done:
        round_num = 0
        pending = rows
        while pending:
            round_num += 1
            logging.info(
                "=== Retry round %s | %s projects pending ===",
                round_num,
                len(pending),
            )
            batch_outcomes = run_rescan_batch(orchestrator, pending)
            merge_outcomes(all_outcomes, batch_outcomes)

            rescanned = sum(1 for o in batch_outcomes if o.status == "rescanned")
            queue_failed = [
                o
                for o in batch_outcomes
                if o.status == "failed" and is_queue_full_reason(o.reason)
            ]
            logging.info(
                "Round %s done: %s rescanned, %s still queue-full",
                round_num,
                rescanned,
                len(queue_failed),
            )

            merged = list(all_outcomes.values())
            write_report(merged, checkpoint)

            if not queue_failed:
                break
            pending = rows_from_outcomes(queue_failed)
            logging.info("Waiting %ss before next round...", args.round_sleep)
            time.sleep(args.round_sleep)

        outcomes = list(all_outcomes.values())
    else:
        outcomes = run_rescan_batch(orchestrator, rows)

    output_path = Path(args.output or default_report_path("rescan_report"))
    csv_path, json_path = write_report(outcomes, output_path)

    still_manual = [o for o in outcomes if o.status in ("manual_review", "failed")]
    queue_still = [o for o in still_manual if is_queue_full_reason(o.reason)]
    manual_path = None
    if still_manual:
        manual_path = write_manual_review_report(
            still_manual,
            args.manual_output or default_report_path("manual_review_remaining"),
        )

    counts = Counter(o.status for o in outcomes)
    print(f"\nDone: {counts.get('rescanned', 0)} rescanned, {len(still_manual)} still need attention")
    if queue_still:
        print(f"Queue-full still pending: {len(queue_still)}")
    print(f"Report: {csv_path}")
    print(f"Summary: {json_path}")
    if manual_path:
        print(f"Still manual: {manual_path} (+ .json)")
    print(f"By status: {dict(counts)}")

    return 0 if not queue_still and counts.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
