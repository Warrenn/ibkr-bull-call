"""Tests for bull_call.ibeam_ssm_provider.

This module is the IBeam-side credential fetcher. Critical security
surface — IBeam calls ``account()`` / ``password()`` and gets the live
IBKR creds back. The credential lifetime, lazy-fetch semantics, and
``clear()``-after-login behaviour are all observable and worth pinning
in tests.

We don't go through real boto3 — ``ibeam_ssm_provider`` constructs
``boto3.client("ssm")`` inside ``_ensure_loaded`` so we monkeypatch
``ssm.fetch_credentials`` to return a scripted ``_Credentials`` object.
"""

from __future__ import annotations

from typing import Any

import pytest

from bull_call import ibeam_ssm_provider as provider_mod
from bull_call.ssm import _Credentials


@pytest.fixture(autouse=True)
def _stub_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``boto3.client`` so the provider's lazy-load path doesn't
    actually try to reach AWS. Each test starts clean."""

    class _FakeSsm:
        pass

    monkeypatch.setattr(
        "boto3.client",
        lambda service_name, region_name=None: _FakeSsm(),
    )


def _set_creds(
    monkeypatch: pytest.MonkeyPatch, *, userid: str = "u1", password: str = "p1",
) -> dict[str, int]:
    """Stub ``fetch_credentials`` and return a counter the test can read."""

    counter = {"calls": 0}

    def fake_fetch(_ssm: Any, *, prefix: str) -> _Credentials:
        counter["calls"] += 1
        return _Credentials(userid=userid, password=password)

    monkeypatch.setattr(provider_mod, "fetch_credentials", fake_fetch)
    return counter


def test_account_and_password_return_loaded_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _set_creds(monkeypatch, userid="ibkr_user", password="ibkr_pass")

    p = provider_mod.SsmSecretsProvider(prefix="/dev/ibkr-bull-call")
    assert p.account() == "ibkr_user"
    assert p.password() == "ibkr_pass"
    # Both calls share one fetch — lazy + memoized.
    assert counter["calls"] == 1


def test_lazy_fetch_does_not_call_ssm_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the lazy path: importing the IBeam plugin /
    constructing the provider must NOT trigger an AWS call. The fetch
    happens on the first ``account()`` / ``password()`` invocation."""

    counter = _set_creds(monkeypatch)

    provider_mod.SsmSecretsProvider(prefix="/dev/ibkr-bull-call")
    assert counter["calls"] == 0


def test_clear_drops_credentials_and_forces_refetch_on_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``clear()`` is the key security guarantee — IBeam invokes it after
    a successful login so the credentials don't linger in process memory.
    A subsequent ``account()`` call should re-fetch from SSM, not return
    cached cleared values."""

    counter = _set_creds(monkeypatch, userid="u", password="p")
    p = provider_mod.SsmSecretsProvider(prefix="/dev/ibkr-bull-call")
    _ = p.account()
    assert counter["calls"] == 1

    p.clear()

    assert p.account() == "u"
    assert counter["calls"] == 2  # re-fetched after clear


def test_prefix_falls_back_to_env_when_unspecified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production wiring relies on ``SSM_PREFIX`` env var (set by the
    systemd EnvironmentFile on EC2). Constructing the provider without
    an explicit prefix must read it from env."""

    counter = _set_creds(monkeypatch)
    monkeypatch.setenv("SSM_PREFIX", "/live/ibkr-bull-call/")  # trailing slash

    p = provider_mod.SsmSecretsProvider()
    assert p._prefix == "/live/ibkr-bull-call"  # trailing slash stripped
    p.account()
    assert counter["calls"] == 1


def test_constructor_raises_when_prefix_neither_arg_nor_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: an unconfigured deploy (forgot the env var, no arg)
    must fail loudly at construction, not silently produce empty SSM
    paths."""

    monkeypatch.delenv("SSM_PREFIX", raising=False)
    with pytest.raises(KeyError, match="SSM_PREFIX"):
        provider_mod.SsmSecretsProvider()


def test_region_falls_back_to_env_then_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_creds(monkeypatch)
    monkeypatch.delenv("AWS_REGION", raising=False)

    # Default fallback.
    p1 = provider_mod.SsmSecretsProvider(prefix="/x")
    assert p1._region == "us-east-1"

    # Env override.
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    p2 = provider_mod.SsmSecretsProvider(prefix="/x")
    assert p2._region == "eu-west-1"

    # Explicit arg overrides both.
    p3 = provider_mod.SsmSecretsProvider(prefix="/x", region="ap-southeast-2")
    assert p3._region == "ap-southeast-2"


def test_account_after_clear_without_fresh_fetch_would_assert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Internal regression: after ``clear()`` zeros the cached
    ``_Credentials.userid`` to None, the provider's ``_ensure_loaded``
    sees ``userid is None`` and re-fetches. If a future refactor breaks
    that fetch and the cache stays at userid=None, ``account()`` would
    hit the ``assert creds.userid is not None`` line. Pin that contract."""

    counter = _set_creds(monkeypatch, userid="u", password="p")
    p = provider_mod.SsmSecretsProvider(prefix="/x")
    p.account()  # populates cache
    p.clear()    # nulls out cached userid + drops the cache reference
    # account() now must do a fresh fetch.
    assert p.account() == "u"
    assert counter["calls"] == 2
