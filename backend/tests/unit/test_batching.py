from __future__ import annotations

import pytest

from migraai.agents.batching import per_session_acu_limit


@pytest.mark.parametrize(
    ("cap", "max_sessions", "expected"),
    [
        (None, 12, None),
        (40, 12, 3),
        (10, 13, 1),
        (12, 12, 1),
        (100, 4, 25),
        (7.5, 2, 3),
    ],
)
def test_per_session_acu_limit(cap, max_sessions, expected) -> None:
    assert per_session_acu_limit(cap, max_sessions) == expected


def test_per_session_acu_limit_requires_a_session_when_cap_is_set() -> None:
    with pytest.raises(ValueError, match="max_sessions must be at least 1"):
        per_session_acu_limit(10, 0)
