"""Shared pytest fixtures."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip env vars our config reads so each test starts clean."""

    for key in list(os.environ):
        if key.startswith(("IB_", "TWS_", "TRADING_MODE", "SYMBOLS", "MAX_LOSS",
                           "POP_THRESHOLD", "RISK_FREE_RATE", "ENTRY_TIME_ET",
                           "STOP_", "STATE_", "LOG_LEVEL", "MIN_PROFIT_TO_LOSS_RATIO",
                           "ENTRY_TIMEOUT_SEC", "ENTRY_DEADLINE_ET",
                           "LEG_FILL_TIMEOUT_SEC", "MONTHLY_",
                           "MONITORING_", "HEARTBEAT_", "SESSION_ERROR_",
                           "AWS_")):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def ddb_table_name(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Spin up an in-memory DynamoDB (via moto) with the bull-call table.

    Yields the table name; tests pass it to ``Store(table_name, region=...)``.
    """

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    from moto import mock_aws

    table_name = f"bull-call-test-{uuid.uuid4().hex[:8]}"
    with mock_aws():
        import boto3

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.get_waiter("table_exists").wait(TableName=table_name)
        yield table_name


@pytest.fixture
def store(ddb_table_name: str):  # type: ignore[no-untyped-def]
    """A fresh DynamoDB-backed Store, scoped to one test."""

    from bull_call.state import Store

    return Store(ddb_table_name, region="us-east-1")
