import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class ProjectOutcome:
    project_id: str
    project_name: str
    origin: str
    main_branch: str
    repo_id: str
    status: str
    scan_id: Optional[str] = None
    reason: Optional[str] = None


def default_report_path(prefix: str = "scan_report") -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path(f"{prefix}_{ts}.csv")


def write_report(outcomes: list[ProjectOutcome], output_path: Path) -> tuple[Path, Path]:
    output_path = Path(output_path)
    json_path = output_path.with_suffix(".json")

    fieldnames = [
        "project_id",
        "project_name",
        "origin",
        "main_branch",
        "repo_id",
        "status",
        "scan_id",
        "reason",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for outcome in outcomes:
            writer.writerow(asdict(outcome))

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_projects": len(outcomes),
        "by_status": {},
        "projects": [asdict(o) for o in outcomes],
    }
    for outcome in outcomes:
        summary["by_status"][outcome.status] = summary["by_status"].get(outcome.status, 0) + 1

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return output_path, json_path


def write_manual_review_report(
    outcomes: list[ProjectOutcome], output_path: Path
) -> Optional[Path]:
    manual = [o for o in outcomes if o.status == "manual_review"]
    if not manual:
        return None

    output_path = Path(output_path)
    json_path = output_path.with_suffix(".json")
    fieldnames = [
        "project_id",
        "project_name",
        "origin",
        "main_branch",
        "repo_id",
        "status",
        "scan_id",
        "reason",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for outcome in manual:
            writer.writerow(asdict(outcome))

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_manual_review": len(manual),
                "projects": [asdict(o) for o in manual],
            },
            f,
            indent=2,
        )

    return output_path
