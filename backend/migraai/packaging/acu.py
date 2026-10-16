from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def total_acus(sessions: Iterable[Any]) -> str:
    session_list = list(sessions)
    values = [float(value) for session in session_list if (value := session.acus) is not None]
    if not values:
        return "not reported by the Devin API"
    total = f"{sum(values):.2f}"
    return f"{total} (partial)" if len(values) < len(session_list) else total
