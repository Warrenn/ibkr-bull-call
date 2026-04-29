"""Custom IBeam secrets provider that fetches IBKR creds from SSM at login time.

Loaded by IBeam at runtime via the IBEAM_SECRETS_SOURCE config knob (or
equivalent extension hook).  IBeam calls ``account()`` and ``password()`` to get
the credentials it'll inject via Selenium into the gateway login form.

Goals:
  - No environment variables hold credentials at any time.
  - Credentials are fetched fresh from SSM at each login (typically once per
    ~24h when the IBKR session expires).
  - The strings live only in this Python process's memory and are dereferenced
    immediately after IBeam consumes them.
"""

from __future__ import annotations

import logging
import os

from bull_call.ssm import _Credentials, fetch_credentials

log = logging.getLogger(__name__)


class SsmSecretsProvider:
    """IBeam-compatible secrets provider.

    IBeam expects ``.account()`` and ``.password()`` methods returning ``str``.
    We fetch lazily so the SSM call happens at login time, not container start.
    """

    def __init__(self, prefix: str | None = None, region: str | None = None) -> None:
        self._prefix = (prefix or os.environ["SSM_PREFIX"]).rstrip("/")
        self._region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._cache: _Credentials | None = None

    def _ensure_loaded(self) -> _Credentials:
        if self._cache is None or self._cache.userid is None:
            import boto3

            ssm = boto3.client("ssm", region_name=self._region)
            self._cache = fetch_credentials(ssm, prefix=self._prefix)
            log.info("fetched IBKR credentials from SSM at %s", self._prefix)
        return self._cache

    def account(self) -> str:
        creds = self._ensure_loaded()
        assert creds.userid is not None
        return creds.userid

    def password(self) -> str:
        creds = self._ensure_loaded()
        assert creds.password is not None
        return creds.password

    def clear(self) -> None:
        """Drop credential references after a successful login."""

        if self._cache is not None:
            self._cache.clear()
            self._cache = None
