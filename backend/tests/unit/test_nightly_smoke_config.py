"""M2-14: the nightly smoke workflow runs `make smoke` at 03:00 UTC and pages Slack on failure."""

from pathlib import Path
from typing import Any

import yaml


def _workflow(repo_root: Path) -> dict[str, Any]:
    data = yaml.safe_load((repo_root / ".github" / "workflows" / "nightly-smoke.yml").read_text())
    assert isinstance(data, dict)
    return data


def test_schedule_and_dispatch(repo_root: Path) -> None:
    wf = _workflow(repo_root)
    triggers = wf.get("on") or wf.get(True)
    assert triggers["schedule"] == [{"cron": "0 3 * * *"}]
    assert "workflow_dispatch" in triggers


def test_job_runs_make_smoke_live_and_pages_slack(repo_root: Path) -> None:
    job = _workflow(repo_root)["jobs"]["live-smoke"]
    assert job["env"]["BIDRADAR_LIVE"] == "1"
    assert "secrets.SAM_API_KEY" in job["env"]["SAM_API_KEY"]
    steps = {str(s.get("name") or s.get("run") or s.get("uses")): s for s in job["steps"]}
    smoke = next(s for s in job["steps"] if "make smoke" in str(s.get("run", "")))
    assert smoke is not None
    page = next(s for s in job["steps"] if s.get("if") == "failure()")
    assert "secrets.OPS_SLACK_WEBHOOK_URL" in page["env"]["OPS_SLACK_WEBHOOK_URL"]
    assert "curl" in page["run"] and "OPS_SLACK_WEBHOOK_URL" in page["run"]
    assert steps  # sanity


def test_makefile_smoke_target(repo_root: Path) -> None:
    text = (repo_root / "Makefile").read_text()
    assert "python -m app.jobs.smoke" in text
