"""Lint regressions for the infra/scripts/ shell helpers.

Skipped if shellcheck is not on PATH so contributors without the tool
installed don't see spurious failures locally — but on any reviewer
or deploy host that has shellcheck (developer-recommended, deploy
hosts have it via package), regressions surface immediately.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "infra" / "scripts"


@pytest.mark.skipif(
    shutil.which("shellcheck") is None,
    reason="shellcheck not installed on this host (`brew install shellcheck`)",
)
@pytest.mark.parametrize(
    "script",
    sorted(p.name for p in _SCRIPTS.glob("*.sh")),
)
def test_shellcheck_clean(script: str) -> None:
    """Each infra script must pass shellcheck cleanly.

    Past offenders (now fixed): SC2064 (eager-expanded trap body that
    couldn't survive a later TAR mutation), SC2162 (``read`` without
    ``-r`` mangling backslashes in IBKR passwords).
    """

    result = subprocess.run(
        ["shellcheck", str(_SCRIPTS / script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"shellcheck flagged {script}:\n{result.stdout}{result.stderr}"
    )
