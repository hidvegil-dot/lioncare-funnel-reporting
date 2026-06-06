from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_REPO = "hidvegil-dot/lioncare-funnel-reporting"
DEFAULT_LOOKBACK_HOURS = 36


@dataclass(frozen=True)
class WorkflowCheck:
    workflow_name: str
    workflow_file: str
    required_steps: tuple[str, ...]
    required_artifact: str | None
    max_age_hours: int


CHECKS: tuple[WorkflowCheck, ...] = (
    WorkflowCheck(
        workflow_name="Daily Funnel Report",
        workflow_file="daily_funnel_report.yml",
        required_steps=(
            "Budapest 05:59 guard",
            "Run daily report",
            "Upload reports to OneDrive",
            "Upload report artifacts",
        ),
        required_artifact="daily-funnel-report",
        max_age_hours=36,
    ),
    WorkflowCheck(
        workflow_name="Weekly GHL Funnel Report",
        workflow_file="weekly_funnel_report.yml",
        required_steps=(
            "Budapest Monday 07:00 guard",
            "Run weekly GHL report",
            "Verify weekly GHL report files",
            "Upload weekly GHL reports to OneDrive",
            "Upload weekly GHL report artifacts",
        ),
        required_artifact="weekly-ghl-funnel-report",
        max_age_hours=8 * 24,
    ),
    WorkflowCheck(
        workflow_name="Fireflies Client Communication AI",
        workflow_file="fireflies_client_communication_ai.yml",
        required_steps=(
            "Run meeting AI batch",
        ),
        required_artifact="meeting-ai-run",
        max_age_hours=36,
    ),
)


class MonitorError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify LionCare GitHub Actions runs beyond green status.")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", DEFAULT_REPO))
    parser.add_argument("--lookback-hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--workflow", action="append", help="Optional workflow name to check. Can be repeated.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN", "").strip()
    selected = set(args.workflow or [])
    checks = [check for check in CHECKS if not selected or check.workflow_name in selected]
    if not checks:
        raise SystemExit("No workflow checks selected")

    failures: list[str] = []
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for check in checks:
        result = _check_workflow(
            repo=args.repo,
            token=token,
            check=check,
            now=now,
            default_lookback_hours=args.lookback_hours,
            failures=failures,
            warnings=warnings,
        )
        results.append(result)

    if args.json:
        print(json.dumps({"ok": not failures, "failures": failures, "warnings": warnings, "results": results}, indent=2))
    else:
        print("LionCare GitHub Actions monitor")
        for result in results:
            print(
                f"{result['workflow_name']}: run={result.get('run_id')} "
                f"event={result.get('event')} conclusion={result.get('conclusion')} "
                f"created_at={result.get('created_at')}"
            )
        for warning in warnings:
            print(f"WARNING: {warning}")
        for failure in failures:
            print(f"FAIL: {failure}")
        if not failures and not warnings:
            print("OK: monitored workflow runs are healthy")

    return 1 if failures else 0


def _check_workflow(
    *,
    repo: str,
    token: str,
    check: WorkflowCheck,
    now: datetime,
    default_lookback_hours: int,
    failures: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    max_age_hours = max(check.max_age_hours, default_lookback_hours if check.workflow_name == "Daily Funnel Report" else 0)
    runs_payload = _github_json(
        repo=repo,
        token=token,
        path=f"/actions/workflows/{check.workflow_file}/runs?per_page=20",
    )
    runs = runs_payload.get("workflow_runs", [])
    expected_cutoff = now - timedelta(hours=max_age_hours)
    candidate = _latest_meaningful_run(repo=repo, token=token, runs=runs, check=check, warnings=warnings)
    result: dict[str, Any] = {"workflow_name": check.workflow_name}

    if candidate is None:
        failures.append(f"{check.workflow_name}: no completed run found")
        return result

    run_id = candidate["id"]
    result.update(
        {
            "run_id": run_id,
            "event": candidate.get("event"),
            "status": candidate.get("status"),
            "conclusion": candidate.get("conclusion"),
            "created_at": candidate.get("created_at"),
            "html_url": candidate.get("html_url"),
        }
    )

    created_at = _parse_github_time(candidate.get("created_at", ""))
    if created_at and created_at < expected_cutoff:
        failures.append(
            f"{check.workflow_name}: latest meaningful run is stale "
            f"created_at={candidate.get('created_at')} max_age_hours={max_age_hours}"
        )

    if candidate.get("conclusion") != "success":
        failures.append(f"{check.workflow_name}: latest meaningful run conclusion={candidate.get('conclusion')}")

    jobs_payload = _github_json(repo=repo, token=token, path=f"/actions/runs/{run_id}/jobs?per_page=100")
    jobs = jobs_payload.get("jobs", [])
    if not jobs:
        failures.append(f"{check.workflow_name}: no jobs found for run {run_id}")
        return result
    result["jobs"] = [{"id": job.get("id"), "name": job.get("name"), "conclusion": job.get("conclusion")} for job in jobs]

    all_steps = [step for job in jobs for step in job.get("steps", [])]
    for step_name in check.required_steps:
        matches = [step for step in all_steps if step.get("name") == step_name]
        if not matches:
            failures.append(f"{check.workflow_name}: missing required step {step_name!r} in run {run_id}")
            continue
        conclusions = {step.get("conclusion") for step in matches}
        if conclusions != {"success"}:
            failures.append(
                f"{check.workflow_name}: required step {step_name!r} not successful in run {run_id}; "
                f"conclusions={sorted(str(item) for item in conclusions)}"
            )

    skipped_required = [
        step.get("name")
        for step in all_steps
        if step.get("name") in check.required_steps and step.get("conclusion") == "skipped"
    ]
    if skipped_required:
        failures.append(f"{check.workflow_name}: required steps skipped in run {run_id}: {', '.join(skipped_required)}")

    if check.required_artifact:
        artifacts_payload = _github_json(repo=repo, token=token, path=f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = artifacts_payload.get("artifacts", [])
        matching_artifacts = [
            artifact
            for artifact in artifacts
            if artifact.get("name") == check.required_artifact and not artifact.get("expired")
        ]
        if not matching_artifacts:
            failures.append(f"{check.workflow_name}: missing artifact {check.required_artifact!r} in run {run_id}")
        elif any(int(artifact.get("size_in_bytes") or 0) <= 0 for artifact in matching_artifacts):
            failures.append(f"{check.workflow_name}: artifact {check.required_artifact!r} has empty size in run {run_id}")
        result["artifacts"] = [
            {
                "id": artifact.get("id"),
                "name": artifact.get("name"),
                "size_in_bytes": artifact.get("size_in_bytes"),
                "expired": artifact.get("expired"),
            }
            for artifact in artifacts
        ]

    return result


def _latest_meaningful_run(
    *,
    repo: str,
    token: str,
    runs: list[dict[str, Any]],
    check: WorkflowCheck,
    warnings: list[str],
) -> dict[str, Any] | None:
    for run in runs:
        if run.get("status") != "completed":
            continue
        if _is_inactive_guard_run(repo=repo, token=token, run=run, check=check):
            warnings.append(
                f"{check.workflow_name}: ignoring inactive schedule guard run "
                f"{run.get('id')} conclusion={run.get('conclusion')}"
            )
            continue
        return run
    return None


def _is_inactive_guard_run(*, repo: str, token: str, run: dict[str, Any], check: WorkflowCheck) -> bool:
    if run.get("event") != "schedule":
        return False
    run_id = run.get("id")
    if not run_id:
        return False
    jobs_payload = _github_json(repo=repo, token=token, path=f"/actions/runs/{run_id}/jobs?per_page=100")
    jobs = jobs_payload.get("jobs", [])
    all_steps = [step for job in jobs for step in job.get("steps", [])]
    required = {step.get("name"): step.get("conclusion") for step in all_steps if step.get("name") in check.required_steps}
    if not required:
        return False
    skipped_required = [name for name, conclusion in required.items() if conclusion == "skipped"]
    inactive_guard_failed = any(
        step.get("name", "").lower().startswith("fail inactive")
        and step.get("conclusion") == "failure"
        for step in all_steps
    )
    return bool(skipped_required) and inactive_guard_failed


def _github_json(*, repo: str, token: str, path: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "lioncare-actions-monitor",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise MonitorError(f"GitHub API error {exc.code} for {path}: {body[:500]}") from exc
    except URLError as exc:
        raise MonitorError(f"GitHub API connection error for {path}: {exc}") from exc
    return json.loads(raw)


def _parse_github_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MonitorError as exc:
        print(f"FAIL: {exc}")
        raise SystemExit(1)
