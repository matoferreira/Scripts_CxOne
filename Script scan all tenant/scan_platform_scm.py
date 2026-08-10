#!/usr/bin/env python3
"""SCM re-scan for Checkmarx projects across multiple branches."""
import argparse
import logging
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from cxone.client import CxOneClient, _parse_scan_engines, load_settings
from cxone.report import ProjectOutcome, default_report_path, write_report
from cxone.scanner import LaunchResult, ScanOrchestrator

DEFAULT_PREFIXES = ("itti-platform/", "itti-platform-iac/")
DEFAULT_EXTRA_BRANCHES = ("prod", "develop")
DEFAULT_EXCLUDE_BRANCHES = ("qa", "uat")


def parse_csv_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def result_to_outcome(result: LaunchResult) -> ProjectOutcome:
    return result.to_outcome()


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="SCM scan for projects matching name prefixes, across multiple branches"
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Include only projects matching these name prefixes (comma-separated)",
    )
    parser.add_argument(
        "--exclude-prefix",
        default="",
        help="Exclude projects matching these name prefixes (comma-separated)",
    )
    parser.add_argument(
        "--extra-branches",
        default=",".join(DEFAULT_EXTRA_BRANCHES),
        help="Extra branches to try (default: prod,develop; main/master always included)",
    )
    parser.add_argument(
        "--exclude-branches",
        default=",".join(DEFAULT_EXCLUDE_BRANCHES),
        help="Branches to skip (default: qa,uat)",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Do not include branches from prior scan history",
    )
    parser.add_argument(
        "--max-branches",
        type=int,
        default=20,
        help="Max branches per project (default: 20)",
    )
    parser.add_argument(
        "--engines",
        default=None,
        help="Comma-separated engines (default: repo config or CX_SCAN_ENGINES)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel project workers (default: CX_SUBMIT_WORKERS)",
    )
    parser.add_argument(
        "--skip-active-check",
        action="store_true",
        help="Launch scans even when project already has active scan",
    )
    parser.add_argument("--dry-run", action="store_true", help="List projects/branches only")
    parser.add_argument("--limit", type=int, default=None, help="Max projects to scan")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Report CSV path")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    include_prefixes = parse_csv_list(args.prefix)
    exclude_prefixes = parse_csv_list(args.exclude_prefix)
    if not include_prefixes and not exclude_prefixes:
        include_prefixes = list(DEFAULT_PREFIXES)

    extra_branches = parse_csv_list(args.extra_branches)
    exclude_branches = frozenset(b.lower() for b in parse_csv_list(args.exclude_branches))

    settings = load_settings()
    if args.engines:
        settings.scan_engines = _parse_scan_engines(args.engines)
    submit_workers = args.workers if args.workers is not None else settings.submit_workers

    client = CxOneClient(settings)
    orchestrator = ScanOrchestrator(
        client=client,
        max_concurrent=settings.max_concurrent,
        submit_workers=submit_workers,
        scan_tag=settings.scan_tag,
        scan_engines=settings.scan_engines,
        skip_active=False,
        wait_for_capacity=False,
    )

    def project_selected(name: str) -> bool:
        if include_prefixes and not ScanOrchestrator.matches_prefixes(name, include_prefixes):
            return False
        if exclude_prefixes and ScanOrchestrator.matches_prefixes(name, exclude_prefixes):
            return False
        return True

    all_projects = orchestrator.list_projects()
    matched = [p for p in all_projects if project_selected(p.get("name", ""))]
    scm_projects = [p for p in matched if orchestrator.is_scm_import(p)]
    non_scm_matched = [p for p in matched if not orchestrator.is_scm_import(p)]

    if args.limit:
        scm_projects = scm_projects[: args.limit]

    filter_desc = []
    if include_prefixes:
        filter_desc.append(f"include={include_prefixes}")
    if exclude_prefixes:
        filter_desc.append(f"exclude={exclude_prefixes}")

    logging.info(
        "Region: %s | Filter: %s | SCM to scan: %s | non-SCM in filter: %s",
        settings.api_host,
        ", ".join(filter_desc) or "all",
        len(scm_projects),
        len(non_scm_matched),
    )

    if args.dry_run:
        total_branches = 0

        def branch_preview(project: dict) -> tuple[str, list[str]]:
            repo_resp = client.get(f"repos-manager/repo/{project['repoId']}")
            repo_cfg = repo_resp.json() if repo_resp.ok else {}
            branches = orchestrator._all_branch_candidates(
                project,
                repo_cfg,
                extra_branches=extra_branches,
                exclude_branches=exclude_branches,
                include_history=not args.no_history,
                max_branches=args.max_branches,
            )
            return project["name"], branches

        with ThreadPoolExecutor(max_workers=submit_workers) as pool:
            for name, branches in pool.map(
                branch_preview, scm_projects, chunksize=10
            ):
                total_branches += len(branches)
                print(f"{name} | branches ({len(branches)}): {', '.join(branches)}")

        print(
            f"\nDry run: {len(scm_projects)} SCM projects, "
            f"{total_branches} total branch scan targets"
        )
        if non_scm_matched:
            print(f"Non-SCM in filter scope: {len(non_scm_matched)}")
        return 0

    orchestrator.prefetch_last_scans([p["id"] for p in scm_projects])

    outcomes: list[ProjectOutcome] = []
    with ThreadPoolExecutor(max_workers=submit_workers) as pool:
        futures = {
            pool.submit(
                orchestrator.launch_scm_all_branches,
                project,
                extra_branches=extra_branches,
                exclude_branches=exclude_branches,
                include_history=not args.no_history,
                max_branches=args.max_branches,
            ): project
            for project in scm_projects
        }
        done_projects = 0
        for fut in as_completed(futures):
            project = futures[fut]
            results = fut.result()
            outcomes.extend(result_to_outcome(r) for r in results)
            done_projects += 1
            ok = sum(1 for r in results if r.success)
            logging.info(
                "Project %s: %s/%s branches launched [%s/%s]",
                project.get("name"),
                ok,
                len(results),
                done_projects,
                len(scm_projects),
            )

    if non_scm_matched and include_prefixes:
        for project in non_scm_matched:
            outcomes.append(
                ProjectOutcome(
                    project_id=project["id"],
                    project_name=project.get("name", project["id"]),
                    origin=project.get("origin") or "",
                    main_branch=project.get("mainBranch") or "",
                    repo_id="",
                    status="skipped_non_scm",
                    reason="Not SCM-import — cannot use projectScan",
                )
            )

    output = args.output or default_report_path("scm_multibranch_scan")
    csv_path, json_path = write_report(outcomes, output)

    counts = Counter(o.status for o in outcomes)
    launched = counts.get("scanned", 0)
    print(f"\nDone: {launched} branch scans launched across {len(scm_projects)} projects")
    print(f"Report: {csv_path}")
    print(f"Summary: {json_path}")
    print(f"By status: {dict(counts)}")

    return 0 if counts.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
