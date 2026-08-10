#!/usr/bin/env python3
"""Launch scans on the first N eligible SCM projects and verify API acceptance."""
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from cxone.client import CxOneClient, load_settings
from cxone.report import default_report_path, write_report
from cxone.scanner import ScanOrchestrator


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Test scan launches on N projects")
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=10,
        help="Number of scans to launch (default: 10)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Report CSV path (JSON summary written alongside)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    settings = load_settings()
    client = CxOneClient(settings)
    orchestrator = ScanOrchestrator(
        client=client,
        max_concurrent=settings.max_concurrent,
        submit_workers=min(args.count, settings.submit_workers),
        scan_tag=settings.scan_tag,
        skip_active=True,
    )

    logging.info("Region: %s | API: %s", settings.iam_host, settings.api_host)

    eligible = [p for p in orchestrator.list_projects() if orchestrator.is_scm_project(p)]
    if len(eligible) < args.count:
        print(f"ERROR: Only {len(eligible)} eligible SCM projects found, need {args.count}")
        return 1

    print(f"Launching {args.count} test scans...")
    results, outcomes = orchestrator.run_bulk(limit=args.count)

    output = args.output or default_report_path("scan_report_test")
    csv_path, json_path = write_report(outcomes, output)

    ok = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    print("\n--- Launch results ---")
    for r in ok:
        print(f"  OK   {r.project_name} | scan_id={r.scan_id}")
    for r in failed:
        print(f"  FAIL {r.project_name} | {r.error}")

    counts = Counter(o.status for o in outcomes)
    print(f"\nSummary: {len(ok)}/{args.count} successful launches")
    print(f"Full tenant report ({len(outcomes)} projects): {csv_path}")
    print(f"JSON summary: {json_path}")
    print(f"By status: {dict(counts)}")

    return 0 if len(ok) == args.count else 1


if __name__ == "__main__":
    sys.exit(main())
