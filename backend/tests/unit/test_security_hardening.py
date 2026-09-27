"""M7-06: the non-code half of the hardening task — scanning config, checklist, drill.

A checklist nobody checks rots into fiction. These tests hold it to three things: every
ASVS chapter is present, every row carries a status from a closed vocabulary, and the
statuses stay honest (a "planned" gap must also appear in the gap list, and the documents
must keep pointing at files that exist).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ASVS_CHAPTERS = (
    "V1 — Architecture, design and threat modelling",
    "V2 — Authentication",
    "V3 — Session management",
    "V4 — Access control",
    "V5 — Validation, sanitisation and encoding",
    "V6 — Stored cryptography",
    "V7 — Error handling and logging",
    "V8 — Data protection",
    "V9 — Communications",
    "V10 — Malicious code",
    "V11 — Business logic",
    "V12 — Files and resources",
    "V13 — API and web service",
    "V14 — Configuration",
)
STATUSES = {"implemented", "partial", "planned", "n/a"}


@pytest.fixture(scope="module")
def asvs(repo_root: Path) -> str:
    return (repo_root / "docs" / "security" / "asvs-l2.md").read_text()


# ------------------------------------------------------------------ dependabot


def test_dependabot_covers_every_ecosystem_we_ship(repo_root: Path) -> None:
    config = yaml.safe_load((repo_root / ".github" / "dependabot.yml").read_text())
    assert config["version"] == 2
    ecosystems = {entry["package-ecosystem"] for entry in config["updates"]}
    # SPEC 11 names pip and npm; the images and the IaC carry CVEs too.
    assert {"uv", "npm", "github-actions", "docker"} <= ecosystems


def test_dependabot_runs_weekly(repo_root: Path) -> None:
    config = yaml.safe_load((repo_root / ".github" / "dependabot.yml").read_text())
    for entry in config["updates"]:
        assert entry["schedule"]["interval"] == "weekly", entry["package-ecosystem"]


def test_dependabot_watches_the_real_directories(repo_root: Path) -> None:
    config = yaml.safe_load((repo_root / ".github" / "dependabot.yml").read_text())
    for entry in config["updates"]:
        directories = entry.get("directories") or [entry["directory"]]
        for directory in directories:
            assert (repo_root / directory.lstrip("/")).is_dir(), directory


# ------------------------------------------------------------------ CI scanning


def test_ci_scans_dependencies_and_secrets(repo_root: Path) -> None:
    workflow = yaml.safe_load((repo_root / ".github" / "workflows" / "ci.yml").read_text())
    job = workflow["jobs"]["security-scan"]
    text = "\n".join(str(step.get("run", "")) + str(step.get("uses", "")) for step in job["steps"])
    assert "pip-audit" in text and "--strict" in text
    assert "pnpm audit --audit-level=high" in text
    assert "gitleaks" in text
    # gitleaks must see the history, not only the tip commit.
    checkout = next(s for s in job["steps"] if "actions/checkout" in str(s.get("uses", "")))
    assert checkout["with"]["fetch-depth"] == 0


# ------------------------------------------------------------------ ASVS checklist


def test_every_asvs_chapter_has_a_section(asvs: str) -> None:
    for chapter in ASVS_CHAPTERS:
        assert f"## {chapter}" in asvs, f"missing ASVS chapter: {chapter}"


def test_every_row_uses_a_known_status(asvs: str) -> None:
    rows = [line for line in asvs.splitlines() if line.startswith("| ") and line.count("|") >= 5]
    statuses = []
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) < 4:
            continue
        status = cells[2].replace("**", "").strip()
        if status in {"Status", "---", "meaning", ""}:
            continue
        statuses.append(status)
    assert statuses, "the checklist has no control rows"
    unknown = {s for s in statuses if s not in STATUSES}
    assert not unknown, f"statuses outside the vocabulary: {sorted(unknown)}"


def test_the_checklist_is_not_all_green(asvs: str) -> None:
    """A checklist with no gaps is a checklist nobody read."""
    assert "**planned**" in asvs
    assert "**partial**" in asvs
    assert "## Open gaps" in asvs


def _resolve(repo_root: Path, citation: str) -> tuple[Path, str] | None:
    """`app/core/uploads.validate_upload` -> (backend/app/core/uploads.py, "validate_upload")."""
    candidate, _, symbol = citation.rpartition(".")
    for path, sym in ((citation, ""), (f"{candidate}.py", symbol)):
        if not path:
            continue
        for root in (repo_root, repo_root / "backend"):
            if (root / path).exists():
                return (root / path), sym
    return None


def test_the_evidence_paths_exist(repo_root: Path, asvs: str) -> None:
    """Every path the checklist cites must be real, and every symbol it names must be in it.

    Evidence that points at a file which was renamed is worse than no evidence: it reads
    as a control that exists.
    """
    cited = set(re.findall(r"`((?:app|backend|infra|docs|tests|\.github|scripts)/[\w./-]+)`", asvs))
    assert len(cited) >= 15, "the checklist cites suspiciously few files"
    missing: list[str] = []
    for citation in sorted(cited):
        resolved = _resolve(repo_root, citation)
        if resolved is None:
            missing.append(f"{citation} (no such file)")
            continue
        path, symbol = resolved
        if symbol and path.is_file() and symbol not in path.read_text():
            missing.append(f"{citation} (no {symbol} in {path.name})")
    assert not missing, f"the ASVS checklist points at things that do not exist: {missing}"


def test_gaps_listed_in_the_summary_match_the_table(asvs: str) -> None:
    summary = asvs.split("## Open gaps", 1)[1]
    for chapter in ("V14.4", "V12.6", "V1.14", "V3.8"):
        assert chapter in summary, f"{chapter} is marked open but not in the gap list"


# ------------------------------------------------------------------ restore drill


def test_restore_drill_script_is_executable_and_strict(repo_root: Path) -> None:
    script = repo_root / "scripts" / "restore_drill.sh"
    assert script.is_file()
    assert script.stat().st_mode & 0o111, "restore_drill.sh is not executable"
    text = script.read_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text


def test_restore_drill_does_what_the_acceptance_asks(repo_root: Path) -> None:
    """List backups -> restore the latest into a scratch instance -> verify -> clean up."""
    text = (repo_root / "scripts" / "restore_drill.sh").read_text()
    assert "gcloud sql backups list" in text
    assert "gcloud sql backups restore" in text
    assert "gcloud sql instances create" in text
    assert "gcloud sql instances delete" in text
    assert "alembic_version" in text, "a restore with no schema must be reported as a failure"
    assert "SELECT count(*)" in text, "a restore that returns zeros everywhere is not a restore"
    assert "--dry-run" in text


def test_restore_drill_never_restores_over_an_existing_instance(repo_root: Path) -> None:
    """`backups restore` into an existing instance OVERWRITES it; that must be impossible."""
    text = (repo_root / "scripts" / "restore_drill.sh").read_text()
    assert "refusing to restore over it" in text
    assert "--restore-instance" in text and '"$SCRATCH"' in text


def test_restore_drill_covers_the_four_environments(repo_root: Path) -> None:
    text = (repo_root / "scripts" / "restore_drill.sh").read_text()
    for env, region in (
        ("dev", "us-east1"),
        ("staging-in", "asia-south1"),
        ("prod-us", "us-east1"),
        ("prod-in", "asia-south1"),
    ):
        assert env in text and region in text


def test_restore_drill_checks_the_spec_11_guarantees(repo_root: Path) -> None:
    text = (repo_root / "scripts" / "restore_drill.sh").read_text()
    assert "pointInTimeRecoveryEnabled" in text
    assert "transactionLogRetentionDays" in text
    assert "SPEC 11 requires 7" in text
    assert "outside" in text and "residency" in text, "backups must not leave the region"


def test_restore_runbook_exists_and_has_a_log(repo_root: Path) -> None:
    runbook = repo_root / "docs" / "runbooks" / "restore-drill.md"
    assert runbook.is_file()
    text = runbook.read_text()
    assert "scripts/restore_drill.sh" in text
    assert "point-in-time" in text.lower()
    assert "## Drill log" in text, "a drill nobody records is a drill nobody repeats"


# ------------------------------------------------------------------ SECURITY.md


def test_security_policy_exists_and_is_honest(repo_root: Path) -> None:
    text = (repo_root / "SECURITY.md").read_text()
    assert "## Reporting a vulnerability" in text
    assert "security@" in text
    assert "## Known gaps" in text, "publishing the gaps is the point"
    assert "docs/security/asvs-l2.md" in text


def test_rate_limit_settings_are_documented(repo_root: Path) -> None:
    env_example = (repo_root / ".env.example").read_text()
    for key in (
        "RATE_LIMIT_ENABLED",
        "RATE_LIMIT_TENANT_PER_MINUTE",
        "RATE_LIMIT_IP_PER_MINUTE",
        "RATE_LIMIT_EXEMPT_PATHS",
    ):
        assert key in env_example


def test_settings_defaults_are_sane() -> None:
    from app.core.config import Settings

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.rate_limit_enabled is True, "the deployed default must be ON"
    assert settings.rate_limit_tenant_per_minute > settings.rate_limit_ip_per_minute, (
        "a tenant is many users behind one or more IPs"
    )
    assert "/healthz" in settings.rate_limit_exempt_paths


def test_exempt_paths_parse_from_a_comma_separated_env_var() -> None:
    from app.core.config import Settings

    settings = Settings(_env_file=None, rate_limit_exempt_paths="/a,/b/c")  # type: ignore[call-arg]
    assert settings.rate_limit_exempt_paths == ["/a", "/b/c"]


def test_a_setting_shaped_like_a_secret_would_be_caught() -> None:
    """Guard on the guard: SECRET_SETTINGS drives Terraform and the Cloud Run manifests."""
    from app.core.config import SECRET_SETTINGS

    assert "auth_secret" in SECRET_SETTINGS
    assert "rate_limit_enabled" not in SECRET_SETTINGS
