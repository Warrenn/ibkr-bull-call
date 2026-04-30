"""Lint regressions for the infra/scripts/ shell helpers.

Skipped if shellcheck is not on PATH so contributors without the tool
installed don't see spurious failures locally — but on any reviewer
or deploy host that has shellcheck, regressions surface immediately.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "infra" / "scripts"
_SCRIPTS = sorted(_SCRIPTS_DIR.glob("*.sh"))


def test_at_least_one_infra_script_exists() -> None:
    """Defensive: a path-rename refactor that empties the glob would
    otherwise make the parametrized shellcheck run zero cases and
    silently pass. Pin the discovery."""

    assert _SCRIPTS, (
        f"no *.sh scripts found under {_SCRIPTS_DIR}; "
        "did the infra layout change?"
    )


@pytest.mark.skipif(
    shutil.which("shellcheck") is None,
    reason="shellcheck not installed on this host",
)
@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda p: p.name)
def test_shellcheck_clean(script: Path) -> None:
    """Each infra script must pass shellcheck cleanly.

    Past offenders (now fixed): SC2064 (eager-expanded trap body that
    couldn't survive a later TAR mutation), SC2162 (``read`` without
    ``-r`` mangling backslashes in IBKR passwords).
    """

    try:
        result = subprocess.run(
            ["shellcheck", str(script)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30.0,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"shellcheck on {script} did not complete within 30s; "
            "PATH or installation may be misconfigured."
        ) from exc

    assert result.returncode == 0, (
        f"shellcheck flagged {script}:\n{result.stdout}{result.stderr}"
    )
