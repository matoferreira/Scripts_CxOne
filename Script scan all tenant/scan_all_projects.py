#!/usr/bin/env python3
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from cxone.client import CxOneClient, load_settings
from cxone.report import default_report_path, write_manual_review_report, write_report
from cxone.scanner import ScanOrchestrator


def build_dry_run_outcomes(orchestrator: ScanOrchestrator, limit: int | None) -> list:
    from cxone.report import ProjectOutcome

    all_projects = orchestrator.list_projects()
    to_scan = all_projects if limit is None else all_projects[:limit]
    to_scan_ids = {p["id"] for p in to_scan}
    outcomes = []

    for project in all_projects:
        fields = orchestrator._project_fields(project)
        project_id = project["id"]
        if project_id not in to_scan_ids:
            status, reason = "skipped_limit", "Not included due to --limit"
        else:
            status, reason = "skipped_dry_run", "Dry run — scan not launched"
        outcomes.append(
            ProjectOutcome(
                project_id=project_id,
                project_name=project.get("name", project_id),
                status=status,
                reason=reason,
                **fields,
            )
        )
    return outcomes


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Launch scans on all Checkmarx One projects in the tenant"
    )
    parser.add_argument("--dry-run", action="store_true", help="List projects only")
    parser.add_argument("--limit", type=int, default=None, help="Max projects to scan")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Report CSV path (JSON summary written alongside)",
    )
    parser.add_argument(
        "--manual-output",
        type=Path,
        default=None,
        help="CSV for projects that need manual testing (default: manual_review_<timestamp>.csv)",
    )
    parser.add_argument(
        "--engines",
        default=None,
        help="Comma-separated engines (e.g. sast). Uses CX_SCAN_ENGINES when omitted.",
    )
    parser.add_argument(
        "--skip-active-check",
        action="store_true",
        help="Launch even if project already has a running/queued scan",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel submit workers (default: CX_SUBMIT_WORKERS)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    settings = load_settings()
    if args.engines:
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
        wait_for_capacity=False,
    )

    logging.info(
        "Region: %s | API: %s | Engines: %s",
        settings.iam_host,
        settings.api_host,
        settings.scan_engines or "all (from repo/last scan)",
    )

    if args.dry_run:
        outcomes = build_dry_run_outcomes(orchestrator, args.limit)
        output = args.output or default_report_path("scan_report_dry_run")
        csv_path, json_path = write_report(outcomes, output)
        counts = Counter(o.status for o in outcomes)
        print(f"Dry run complete. Report: {csv_path} | Summary: {json_path}")
        print(f"Projects: {len(outcomes)} | {dict(counts)}")
        return 0

    _, outcomes = orchestrator.run_bulk(limit=args.limit)
    output = args.output or default_report_path("scan_report")
    csv_path, json_path = write_report(outcomes, output)

    manual_path = args.manual_output or default_report_path("manual_review")
    manual_csv = write_manual_review_report(outcomes, manual_path)

    launch_counts = Counter(o.status for o in outcomes)
    scanned = launch_counts.get("scanned", 0)
    failed = launch_counts.get("failed", 0)
    manual = launch_counts.get("manual_review", 0)

    print(f"\nDone: {scanned} scanned, {failed} failed, {manual} manual review")
    print(f"Total projects: {len(outcomes)}")
    print(f"Report: {csv_path}")
    print(f"Summary: {json_path}")
    if manual_csv:
        print(f"Manual review: {manual_csv} (+ .json)")
    print(f"By status: {dict(launch_counts)}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
