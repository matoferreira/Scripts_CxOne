import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from .client import CxOneClient
from .report import ProjectOutcome

log = logging.getLogger(__name__)

ENGINE_MAP = {
    "sastScannerEnabled": "sast",
    "scaScannerEnabled": "sca",
    "kicsScannerEnabled": "kics",
    "apiSecScannerEnabled": "apisec",
    "containerScannerEnabled": "containers",
    "ossfScoreCardScannerEnabled": "scorecard",
    "secretsDetectionScannerEnabled": "2ms",
}

DEFAULT_BRANCH_FALLBACKS = ("main", "master")
DEFAULT_EXCLUDED_BRANCHES = frozenset({"qa", "uat"})
BRANCH_ERROR_MARKERS = (
    "no branch",
    "branch configured",
    "branch not found",
    "invalid branch",
    "unknown branch",
)


@dataclass
class LaunchResult:
    project_id: str
    project_name: str
    success: bool
    scan_id: Optional[str] = None
    error: Optional[str] = None
    origin: str = ""
    main_branch: str = ""
    repo_id: str = ""
    status: str = ""
    branch_used: str = ""

    def to_outcome(self) -> ProjectOutcome:
        if self.status:
            outcome_status = self.status
        elif self.success:
            outcome_status = "scanned"
        else:
            outcome_status = "failed"
        reason = self.error
        if self.branch_used and reason:
            reason = f"branch={self.branch_used}; {reason}"
        elif self.branch_used:
            reason = f"branch={self.branch_used}"
        return ProjectOutcome(
            project_id=self.project_id,
            project_name=self.project_name,
            origin=self.origin,
            main_branch=self.branch_used or self.main_branch,
            repo_id=self.repo_id,
            status=outcome_status,
            scan_id=self.scan_id,
            reason=reason,
        )


@dataclass
class ScanOrchestrator:
    client: CxOneClient
    max_concurrent: int = 800
    submit_workers: int = 50
    scan_tag: str = "bulk-scan"
    scan_engines: Optional[list[str]] = None
    skip_active: bool = True
    wait_for_capacity: bool = False
    _active_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cache_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_scan_cache: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _project_config_cache: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)

    def list_projects(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        offset = 0
        page_size = 500
        while True:
            resp = self.client.get(
                "projects/",
                params={"limit": page_size, "offset": offset},
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("projects", [])
            projects.extend(batch)
            total = data.get("filteredTotalCount", data.get("totalCount", 0))
            offset += len(batch)
            if not batch or offset >= total:
                break
        return projects

    def prefetch_last_scans(self, project_ids: list[str]) -> None:
        batch_size = 100
        for i in range(0, len(project_ids), batch_size):
            batch = project_ids[i : i + batch_size]
            resp = self.client.get(
                "projects/last-scan",
                params={"project-ids": ",".join(batch)},
            )
            if not resp.ok:
                log.warning("last-scan batch failed: %s", resp.status_code)
                continue
            data = resp.json()
            if isinstance(data, dict):
                with self._cache_lock:
                    self._last_scan_cache.update(data)

    def count_active_scans(self) -> int:
        resp = self.client.get(
            "scans",
            params={"limit": 1, "offset": 0, "statuses": "Running,Queued"},
        )
        if resp.ok:
            return resp.json().get("filteredTotalCount", 0)
        return 0

    def _wait_for_capacity(self) -> None:
        if not self.wait_for_capacity:
            return
        while True:
            active = self.count_active_scans()
            if active < self.max_concurrent:
                return
            log.info("Active scans %s / %s — waiting...", active, self.max_concurrent)
            time.sleep(10)

    @staticmethod
    def is_scm_import(project: dict[str, Any]) -> bool:
        return bool(project.get("repoId"))

    @staticmethod
    def _all_branches_from_repo_cfg(repo_cfg: dict[str, Any]) -> list[str]:
        branches: list[str] = []
        for branch in repo_cfg.get("branches", []):
            name = branch.get("pattern") or branch.get("name") or ""
            if name:
                branches.append(name)
        return branches

    def _scan_history_branches(self, project_id: str, *, page_size: int = 100, max_pages: int = 5) -> list[str]:
        branches: list[str] = []
        offset = 0
        for _ in range(max_pages):
            resp = self.client.get(
                "scans",
                params={
                    "limit": page_size,
                    "offset": offset,
                    "project-ids": project_id,
                },
            )
            if not resp.ok:
                break
            batch = resp.json().get("scans", [])
            for scan in batch:
                branch = scan.get("branch") or ""
                if branch:
                    branches.append(branch)
            if len(batch) < page_size:
                break
            offset += len(batch)
        return branches

    @staticmethod
    def _is_excluded_branch(branch: str, excluded: frozenset[str]) -> bool:
        key = branch.strip().lower()
        if not key:
            return True
        if key in excluded:
            return True
        # Skip qa/uat env branches like env/qa, release/uat
        for part in key.split("/"):
            if part in excluded:
                return True
        return False

    def _all_branch_candidates(
        self,
        project: dict[str, Any],
        repo_cfg: Optional[dict[str, Any]] = None,
        *,
        extra_branches: Optional[list[str]] = None,
        exclude_branches: Optional[frozenset[str]] = None,
        include_history: bool = True,
        max_branches: int = 20,
    ) -> list[str]:
        excluded = exclude_branches if exclude_branches is not None else DEFAULT_EXCLUDED_BRANCHES
        candidates: list[str] = []
        if repo_cfg:
            candidates.extend(self._all_branches_from_repo_cfg(repo_cfg))
        if project.get("mainBranch"):
            candidates.append(project["mainBranch"])

        git_cfg = self._get_git_config(project["id"])
        if git_cfg.get("branch"):
            candidates.append(git_cfg["branch"])

        if include_history:
            candidates.extend(self._scan_history_branches(project["id"]))

        last_scan = self._get_last_scan(project["id"])
        if last_scan and last_scan.get("branch"):
            candidates.append(last_scan["branch"])

        if extra_branches:
            candidates.extend(extra_branches)

        candidates.extend(DEFAULT_BRANCH_FALLBACKS)
        deduped = [
            b for b in self._dedupe(candidates)
            if not self._is_excluded_branch(b, excluded)
        ]
        return deduped[:max_branches]

    def _launch_scm_branch_scan(
        self,
        project: dict[str, Any],
        repo_cfg: dict[str, Any],
        branch: str,
    ) -> LaunchResult:
        repo_id = project["repoId"]
        scm_id = repo_cfg.get("scmId")
        if not scm_id:
            return self._result(
                project,
                success=False,
                status="failed",
                error="Missing scmId in repo config",
                branch_used=branch,
            )

        repo_url = repo_cfg.get("url") or project.get("repoUrl", "")
        scm_org = self._scm_org_from_url(repo_url)
        scanners = self._selected_scanners(repo_cfg)
        payload: dict[str, Any] = {
            "repoOrigin": project.get("origin") or repo_cfg.get("type", ""),
            "project": {
                "repoIdentity": project.get("scmRepoId", ""),
                "repoUrl": repo_url,
                "projectId": project["id"],
                "defaultBranch": branch,
                "scannerTypes": scanners,
                "repoId": repo_id,
            },
        }
        if self.scan_tag:
            payload["tags"] = {self.scan_tag: ""}

        path = (
            f"repos-manager/scms/{scm_id}/orgs/{scm_org}/repo/projectScan"
            f"?projectId={project['id']}"
        )
        resp = self.client.post(path, json=payload)
        if resp.ok:
            scan_id = self._extract_scan_id(resp)
            if not scan_id:
                scan_id = self._latest_scan_id(project["id"], branch)
            return self._result(
                project,
                success=True,
                status="scanned",
                scan_id=scan_id,
                branch_used=branch,
            )

        return self._result(
            project,
            success=False,
            status="failed",
            error=f"projectScan {resp.status_code}: {resp.text[:300]}",
            branch_used=branch,
        )

    def _latest_scan_id(self, project_id: str, branch: str) -> Optional[str]:
        resp = self.client.get(
            "scans",
            params={"limit": 1, "project-ids": project_id, "branch": branch},
        )
        if not resp.ok:
            return None
        scans = resp.json().get("scans", [])
        return scans[0].get("id") if scans else None

    def launch_scm_all_branches(
        self,
        project: dict[str, Any],
        *,
        extra_branches: Optional[list[str]] = None,
        exclude_branches: Optional[frozenset[str]] = None,
        include_history: bool = True,
        max_branches: int = 20,
    ) -> list[LaunchResult]:
        repo_id = project.get("repoId")
        if not repo_id:
            return [
                self._result(
                    project,
                    success=False,
                    status="manual_review",
                    error="Not an SCM-import project",
                )
            ]

        try:
            self._wait_for_capacity()
            repo_resp = self.client.get(f"repos-manager/repo/{repo_id}")
            if not repo_resp.ok:
                return [
                    self._result(
                        project,
                        success=False,
                        status="failed",
                        error=f"Repo config failed: {repo_resp.status_code} {repo_resp.text[:200]}",
                    )
                ]

            repo_cfg = repo_resp.json()
            branches = self._all_branch_candidates(
                project,
                repo_cfg,
                extra_branches=extra_branches,
                exclude_branches=exclude_branches,
                include_history=include_history,
                max_branches=max_branches,
            )
            if not branches:
                branches = list(DEFAULT_BRANCH_FALLBACKS)

            results: list[LaunchResult] = []
            for branch in branches:
                if self.skip_active and self._project_has_active_scan(project["id"]):
                    results.append(
                        self._result(
                            project,
                            success=False,
                            status="skipped_active",
                            error="Already has active scan",
                            branch_used=branch,
                        )
                    )
                    continue
                results.append(self._launch_scm_branch_scan(project, repo_cfg, branch))
            return results
        except Exception as exc:
            return [
                self._result(
                    project,
                    success=False,
                    status="failed",
                    error=str(exc)[:500],
                )
            ]

    @staticmethod
    def matches_prefixes(name: str, prefixes: list[str]) -> bool:
        return any(name.startswith(prefix) for prefix in prefixes)

    @staticmethod
    def _branch_from_repo_cfg(repo_cfg: dict[str, Any], fallback: str) -> str:
        if fallback:
            return fallback
        for branch in repo_cfg.get("branches", []):
            if branch.get("isDefaultBranch") or len(repo_cfg.get("branches", [])) == 1:
                return branch.get("name") or branch.get("pattern", "")
        return ""

    def _selected_scanners(self, repo_cfg: Optional[dict[str, Any]] = None) -> list[str]:
        if self.scan_engines:
            return list(self.scan_engines)
        if repo_cfg:
            scanners = [v for k, v in ENGINE_MAP.items() if repo_cfg.get(k)]
            if scanners:
                return scanners
        return ["sast"]

    @staticmethod
    def _scanners_from_repo_cfg(repo_cfg: dict[str, Any]) -> list[str]:
        scanners = [v for k, v in ENGINE_MAP.items() if repo_cfg.get(k)]
        return scanners or ["sast"]

    @staticmethod
    def _scm_org_from_url(repo_url: str) -> str:
        if not repo_url:
            return "anyorg"
        path = urlparse(repo_url).path.strip("/")
        parts = path.split("/")
        if len(parts) >= 2:
            return parts[0]
        return "anyorg"

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            key = item.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    @staticmethod
    def _is_branch_error(error: str) -> bool:
        lower = error.lower()
        return any(marker in lower for marker in BRANCH_ERROR_MARKERS)

    def _get_last_scan(self, project_id: str) -> Optional[dict[str, Any]]:
        with self._cache_lock:
            if project_id in self._last_scan_cache:
                return self._last_scan_cache[project_id]
        resp = self.client.get(
            "projects/last-scan",
            params={"project-ids": project_id},
        )
        if not resp.ok:
            return None
        data = resp.json()
        scan = data.get(project_id) if isinstance(data, dict) else None
        if scan:
            with self._cache_lock:
                self._last_scan_cache[project_id] = scan
        return scan

    def _get_git_config(self, project_id: str) -> dict[str, str]:
        with self._cache_lock:
            if project_id in self._project_config_cache:
                return self._project_config_cache[project_id]

        result = {"repo_url": "", "branch": ""}
        resp = self.client.get(
            "configuration/project",
            params={"project-id": project_id},
        )
        if resp.ok:
            for entry in resp.json():
                key = entry.get("key", "")
                value = entry.get("value") or ""
                if key == "scan.handler.git.repository":
                    result["repo_url"] = value
                elif key == "scan.handler.git.branch":
                    result["branch"] = value

        with self._cache_lock:
            self._project_config_cache[project_id] = result
        return result

    def _branch_candidates(
        self, project: dict[str, Any], repo_cfg: Optional[dict[str, Any]] = None
    ) -> list[str]:
        candidates: list[str] = []
        if project.get("mainBranch"):
            candidates.append(project["mainBranch"])
        if repo_cfg:
            from_repo = self._branch_from_repo_cfg(repo_cfg, "")
            if from_repo:
                candidates.append(from_repo)

        git_cfg = self._get_git_config(project["id"])
        if git_cfg.get("branch"):
            candidates.append(git_cfg["branch"])

        last_scan = self._get_last_scan(project["id"])
        if last_scan and last_scan.get("branch"):
            candidates.append(last_scan["branch"])

        candidates.extend(DEFAULT_BRANCH_FALLBACKS)
        return self._dedupe(candidates)

    def _project_has_active_scan(self, project_id: str) -> bool:
        resp = self.client.get(
            "scans",
            params={
                "limit": 1,
                "project-ids": project_id,
                "statuses": "Running,Queued",
            },
        )
        if not resp.ok:
            return False
        return resp.json().get("filteredTotalCount", 0) > 0

    @staticmethod
    def _project_fields(project: dict[str, Any]) -> dict[str, str]:
        repo_id = project.get("repoId")
        return {
            "origin": project.get("origin") or "",
            "main_branch": project.get("mainBranch") or "",
            "repo_id": str(repo_id) if repo_id is not None else "",
        }

    def _result(
        self,
        project: dict[str, Any],
        *,
        success: bool,
        status: str,
        scan_id: Optional[str] = None,
        error: Optional[str] = None,
        branch_used: str = "",
    ) -> LaunchResult:
        fields = self._project_fields(project)
        return LaunchResult(
            project_id=project["id"],
            project_name=project.get("name", project["id"]),
            success=success,
            scan_id=scan_id,
            error=error,
            status=status,
            branch_used=branch_used,
            **fields,
        )

    def _resolve_repo_url(self, project: dict[str, Any]) -> str:
        if project.get("repoUrl"):
            return project["repoUrl"]
        git_cfg = self._get_git_config(project["id"])
        if git_cfg.get("repo_url"):
            return git_cfg["repo_url"]
        last_scan = self._get_last_scan(project["id"])
        if last_scan and last_scan.get("repoUrl"):
            return last_scan["repoUrl"]
        return ""

    def launch_scan(self, project: dict[str, Any]) -> LaunchResult:
        if self.skip_active and self._project_has_active_scan(project["id"]):
            return self._result(
                project,
                success=False,
                status="skipped_active",
                error="Already has active scan",
            )

        try:
            if self.is_scm_import(project):
                return self._launch_scm_import_scan(project, project["repoId"])

            if not self.is_scm_import(project):
                rescan = self._try_rescan(project)
                if rescan is not None:
                    return rescan

            repo_url = self._resolve_repo_url(project)
            if repo_url:
                return self._launch_git_scan(project, repo_url)

            return self._result(
                project,
                success=False,
                status="manual_review",
                error="No prior scan to rescan and no repo URL — test manually",
            )
        except Exception as exc:
            return self._result(
                project,
                success=False,
                status="failed",
                error=str(exc),
            )

    def _launch_scm_import_scan(self, project: dict[str, Any], repo_id: int) -> LaunchResult:
        repo_resp = self.client.get(f"repos-manager/repo/{repo_id}")
        if not repo_resp.ok:
            return self._result(
                project,
                success=False,
                status="failed",
                error=f"Repo config failed: {repo_resp.status_code} {repo_resp.text[:200]}",
            )

        repo_cfg = repo_resp.json()
        scm_id = repo_cfg.get("scmId")
        if not scm_id:
            return self._result(
                project,
                success=False,
                status="failed",
                error="Missing scmId in repo config",
            )

        repo_url = repo_cfg.get("url") or project.get("repoUrl", "")
        scm_org = self._scm_org_from_url(repo_url)
        scanners = self._selected_scanners(repo_cfg)
        branches = self._branch_candidates(project, repo_cfg)
        if not branches:
            branches = list(DEFAULT_BRANCH_FALLBACKS)

        last_error = "No branch configured"
        for branch in branches:
            payload: dict[str, Any] = {
                "repoOrigin": project.get("origin") or repo_cfg.get("type", ""),
                "project": {
                    "repoIdentity": project.get("scmRepoId", ""),
                    "repoUrl": repo_url,
                    "projectId": project["id"],
                    "defaultBranch": branch,
                    "scannerTypes": scanners,
                    "repoId": repo_id,
                },
            }
            if self.scan_tag:
                payload["tags"] = {self.scan_tag: ""}

            path = (
                f"repos-manager/scms/{scm_id}/orgs/{scm_org}/repo/projectScan"
                f"?projectId={project['id']}"
            )
            resp = self.client.post(path, json=payload)
            if resp.ok:
                return self._result(
                    project,
                    success=True,
                    status="scanned",
                    scan_id=self._extract_scan_id(resp),
                    branch_used=branch,
                )

            last_error = f"projectScan {resp.status_code}: {resp.text[:300]}"
            if not self._is_branch_error(last_error):
                break

        return self._result(
            project,
            success=False,
            status="failed",
            error=last_error,
            branch_used=branches[-1] if branches else "",
        )

    def _engine_config(self, project_id: str = "") -> list[dict[str, Any]]:
        if self.scan_engines:
            return [{"type": engine, "value": {}} for engine in self.scan_engines]
        last_scan = self._get_last_scan(project_id) if project_id else None
        if last_scan and last_scan.get("engines"):
            return [{"type": engine, "value": {}} for engine in last_scan["engines"]]
        return [{"type": "sast", "value": {}}]

    def _engines_for_project(self, project_id: str) -> list[dict[str, Any]]:
        return self._engine_config(project_id)

    def _launch_git_scan(self, project: dict[str, Any], repo_url: str) -> LaunchResult:
        branches = self._branch_candidates(project)
        if not branches:
            branches = list(DEFAULT_BRANCH_FALLBACKS)

        last_error = "Git scan failed"
        for branch in branches:
            payload: dict[str, Any] = {
                "type": "git",
                "project": {"id": project["id"]},
                "handler": {"branch": branch, "repoUrl": repo_url},
                "config": self._engines_for_project(project["id"]),
            }
            if self.scan_tag:
                payload["tags"] = {self.scan_tag: ""}

            resp = self.client.post("scans", json=payload)
            if resp.ok:
                return self._result(
                    project,
                    success=True,
                    status="scanned",
                    scan_id=self._extract_scan_id(resp),
                    branch_used=branch,
                )

            last_error = f"POST /scans {resp.status_code}: {resp.text[:300]}"
            if not self._is_branch_error(last_error):
                break

        return self._result(
            project,
            success=False,
            status="failed",
            error=last_error,
            branch_used=branches[-1] if branches else "",
        )

    def _try_rescan(self, project: dict[str, Any]) -> Optional[LaunchResult]:
        """Re-scan from stored source (no re-upload).

        Default: POST /api/scans/rescan with project_id.
        SAST/engine-specific: POST /api/scans type=rescan with config engines.
        """
        last_scan = self._get_last_scan(project["id"])
        if not last_scan:
            return None

        branch = last_scan.get("branch") or ""
        last_error = ""

        for attempt in range(12):
            if self.scan_engines:
                payload: dict[str, Any] = {
                    "type": "rescan",
                    "project": {"id": project["id"]},
                    "handler": {"project_id": project["id"]},
                    "config": self._engine_config(project["id"]),
                }
                if self.scan_tag:
                    payload["tags"] = {self.scan_tag: ""}
                resp = self.client.post("scans", json=payload)
            else:
                payload = {"project_id": project["id"]}
                resp = self.client.post("scans/rescan", json=payload)
            if resp.ok:
                return self._result(
                    project,
                    success=True,
                    status="rescanned",
                    scan_id=self._extract_scan_id(resp),
                    branch_used=branch,
                )

            last_error = f"POST /scans/rescan {resp.status_code}: {resp.text[:300]}"
            if (
                resp.status_code in (429, 500, 503)
                and "Max Queued" in resp.text
                and attempt < 11
            ):
                sleep_s = min(15 + attempt * 5, 60)
                log.debug(
                    "Queue full for %s — retry %s/%s in %ss",
                    project.get("name", project["id"]),
                    attempt + 1,
                    12,
                    sleep_s,
                )
                time.sleep(sleep_s)
                continue
            break

        return self._result(
            project,
            success=False,
            status="failed",
            error=last_error,
            branch_used=branch,
        )

    def rescan_project_by_id(
        self, project_id: str, project_name: str = ""
    ) -> LaunchResult:
        """Trigger rescan for a project ID (e.g. from manual review CSV)."""
        project = {
            "id": project_id,
            "name": project_name or project_id,
        }
        try:
            self._wait_for_capacity()
            if self.skip_active and self._project_has_active_scan(project_id):
                return self._result(
                    project,
                    success=False,
                    status="skipped_active",
                    error="Already has active scan",
                )
            result = self._try_rescan(project)
            if result is not None:
                return result
            return self._result(
                project,
                success=False,
                status="manual_review",
                error="No prior scan available for rescan",
            )
        except Exception as exc:
            return self._result(
                project,
                success=False,
                status="failed",
                error=str(exc)[:500],
            )

    @staticmethod
    def _extract_scan_id(resp) -> Optional[str]:
        try:
            body = resp.json()
        except Exception:
            return None
        if isinstance(body, dict):
            return body.get("id") or body.get("scanId")
        return None

    def run_bulk(self, limit: Optional[int] = None) -> tuple[list[LaunchResult], list[ProjectOutcome]]:
        all_projects = self.list_projects()
        to_scan = all_projects if limit is None else all_projects[:limit]
        to_scan_ids = {p["id"] for p in to_scan}

        log.info("Total projects: %s | To scan: %s", len(all_projects), len(to_scan))
        self.prefetch_last_scans([p["id"] for p in to_scan])

        results: list[LaunchResult] = []

        with ThreadPoolExecutor(max_workers=self.submit_workers) as pool:
            futures = {}
            for i, project in enumerate(to_scan):
                if i % 25 == 0:
                    self._wait_for_capacity()
                fut = pool.submit(self.launch_scan, project)
                futures[fut] = project
                if (i + 1) % 100 == 0:
                    log.info("Submitted %s / %s scan jobs", i + 1, len(to_scan))

            completed = 0
            for fut in as_completed(futures):
                result = fut.result()
                results.append(result)
                completed += 1
                if result.success:
                    log.info(
                        "Launched %s (%s) branch=%s [%s/%s]",
                        result.project_name,
                        result.project_id,
                        result.branch_used or result.main_branch,
                        completed,
                        len(to_scan),
                    )
                elif result.status == "manual_review":
                    log.warning(
                        "Manual review %s (%s): %s [%s/%s]",
                        result.project_name,
                        result.project_id,
                        result.error,
                        completed,
                        len(to_scan),
                    )
                elif result.status != "skipped_active":
                    log.warning(
                        "Failed %s (%s): %s [%s/%s]",
                        result.project_name,
                        result.project_id,
                        result.error,
                        completed,
                        len(to_scan),
                    )

        results_by_id = {r.project_id: r for r in results}
        outcomes: list[ProjectOutcome] = []

        for project in all_projects:
            project_id = project["id"]
            fields = self._project_fields(project)

            if project_id in results_by_id:
                outcomes.append(results_by_id[project_id].to_outcome())
            elif project_id not in to_scan_ids:
                outcomes.append(
                    ProjectOutcome(
                        project_id=project_id,
                        project_name=project.get("name", project_id),
                        status="skipped_limit",
                        reason="Not included due to --limit",
                        **fields,
                    )
                )
            else:
                outcomes.append(
                    ProjectOutcome(
                        project_id=project_id,
                        project_name=project.get("name", project_id),
                        status="skipped_unknown",
                        reason="Scan was not attempted",
                        **fields,
                    )
                )

        return results, outcomes
