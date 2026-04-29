"""Structured JSON event logger for state transitions.

Every state transition the bot makes (open, close, settle, stop-armed,
stop-fired, stop-suppressed, stop-uneconomic, legout-flattened, position-
adopted, entry-cancelled) emits one line of JSON to the standard logger.
The CloudWatch agent ships these to a stream where they're queryable via
CloudWatch Logs Insights — a parallel "trade journal" alongside DynamoDB.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

log = logging.getLogger("bull_call.events")


def emit(event: str, **fields: Any) -> None:
    """Log one structured event line.

    The line is plain JSON (one object per line) so CloudWatch Logs Insights
    can parse it via the JSON parser without a custom log format.
    """

    payload: dict[str, Any] = {
        "event": event,
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    for key, value in fields.items():
        if value is None:
            continue
        payload[key] = value
    log.info(json.dumps(payload, default=str))
