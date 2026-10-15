from __future__ import annotations

from pathlib import Path

from migraai.core.secrets import DevFileKeyProtector
from migraai.core.settings_store import SettingsStore, SettingsUpdate


def test_settings_defaults_and_encrypted_pat_storage(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path, DevFileKeyProtector(tmp_path))
    store.initialize()
    assert store.read().confidence_threshold == 0.85
    assert store.read().max_concurrent_sessions == 4
    assert store.read().plan_acu_cap == 10
    assert store.read().plan_timeout_minutes == 30
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
    }
    result = store.read()
    assert result.devin_pat.configured
    assert result.devin_pat.masked == "••••-pat"
    assert result.devin_org_id == "org-1"
    assert result.confidence_threshold == 0.9
    assert result.max_concurrent_sessions == 8
    assert result.plan_acu_cap == 25
    assert result.plan_timeout_minutes == 45

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
    assert store.runtime_devin_config()[1] == "https://api.devinenterprise.com"

    store.update(SettingsUpdate(devin_pat="cog_personal"))
    assert store.read().token_kind == "v3"
    store.update(SettingsUpdate(devin_pat="unsupported"))
    assert store.read().token_kind == "none"
    store.update(SettingsUpdate(devin_pat=""))
    assert store.read().token_kind == "none"
