from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from migraai.core.secrets import DevFileKeyProtector
from migraai.core.settings_store import SettingsStore, SettingsUpdate


def test_settings_defaults_and_encrypted_pat_storage(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path, DevFileKeyProtector(tmp_path))
    store.initialize()
    assert store.read().confidence_threshold == 0.85
    assert store.read().max_concurrent_sessions == 4
    assert store.read().plan_acu_cap == 10
    assert store.read().plan_timeout_minutes == 30
    assert store.read().conversion_batch_size == 20
    assert store.read().conversion_session_timeout_minutes == 45
    assert store.read().token_kind == "none"
    assert store.read().devin_api_base_url == "https://api.devin.ai"
    assert not store.read().devin_pat.configured

    changed = store.update(
        SettingsUpdate(
            devin_pat="my-enterprise-pat",
            devin_org_id="org-1",
            confidence_threshold=0.9,
            max_concurrent_sessions=8,
            acu_cap_per_run=50,
            plan_acu_cap=25,
            plan_timeout_minutes=45,
            conversion_batch_size=35,
            conversion_session_timeout_minutes=60,
        )
    )
    assert changed == {
        "devin_pat",
        "devin_org_id",
        "confidence_threshold",
        "max_concurrent_sessions",
        "acu_cap_per_run",
        "plan_acu_cap",
        "plan_timeout_minutes",
        "conversion_batch_size",
        "conversion_session_timeout_minutes",
    }
    result = store.read()
    assert result.devin_pat.configured
    assert result.devin_pat.masked == "••••-pat"
    assert result.devin_org_id == "org-1"
    assert result.confidence_threshold == 0.9
    assert result.max_concurrent_sessions == 8
    assert result.plan_acu_cap == 25
    assert result.plan_timeout_minutes == 45
    assert result.conversion_batch_size == 35
    assert result.conversion_session_timeout_minutes == 60

    store.update(SettingsUpdate(devin_pat=""))
    assert not store.read().devin_pat.configured
    assert b"my-enterprise-pat" not in (tmp_path / "app.db").read_bytes()


def test_settings_normalize_api_base_url_and_classify_token_prefix(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path, DevFileKeyProtector(tmp_path))
    store.initialize()
    entered = "https://api.devinenterprise.com/v1/sessions?limit=2#fragment"
    store.update(SettingsUpdate(devin_api_base_url=entered, devin_pat="apk_legacy"))

    assert store.read().devin_api_base_url == entered
    assert store.read().token_kind == "legacy"
    assert store.read().devin_org_id == ""
    assert store.runtime_devin_config()[1] == "https://api.devinenterprise.com"

    store.update(SettingsUpdate(devin_pat="cog_personal"))
    assert store.read().token_kind == "v3"
    store.update(SettingsUpdate(devin_pat="unsupported"))
    assert store.read().token_kind == "none"
    store.update(SettingsUpdate(devin_pat=""))
    assert store.read().token_kind == "none"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("conversion_batch_size", 0),
        ("conversion_batch_size", 51),
        ("conversion_session_timeout_minutes", 4),
        ("conversion_session_timeout_minutes", 241),
    ],
)
def test_conversion_settings_api_validation_ranges(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        SettingsUpdate(**{field: value})
