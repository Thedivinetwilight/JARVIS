"""Capability manifests: scope vocabulary, least privilege and honest availability."""
from __future__ import annotations

import pytest

from jarvis import manifests
from jarvis.manifests import (
    ADMIN_SCOPES,
    ALL_SCOPES,
    DEFAULT_DEVICE_SCOPES,
    MANIFESTS,
    OPERATOR_SCOPES,
    get_manifest,
    manifests_dict,
    validate_scopes,
)


def test_every_declared_scope_is_known_and_sorted():
    assert validate_scopes(["tasks.write", "tasks.read", "tasks.write"]) == ("tasks.read", "tasks.write")
    with pytest.raises(ValueError, match="Unknown scopes"):
        validate_scopes(["tasks.destroy"])


def test_device_scopes_never_include_administration():
    assert not set(DEFAULT_DEVICE_SCOPES) & ADMIN_SCOPES
    assert set(DEFAULT_DEVICE_SCOPES) <= OPERATOR_SCOPES <= ALL_SCOPES
    assert manifests.SCOPE_CLIENTS_REVOKE in OPERATOR_SCOPES
    assert manifests.SCOPE_PLANS_WRITE in OPERATOR_SCOPES
    assert manifests.SCOPE_PLANS_WRITE not in DEFAULT_DEVICE_SCOPES


def test_manifest_scopes_are_all_declared_and_unique_per_manifest():
    for manifest in MANIFESTS:
        assert set(manifest.scopes) <= ALL_SCOPES, manifest.id
    ids = [m.id for m in MANIFESTS]
    assert len(ids) == len(set(ids))
    assert {m.kind for m in MANIFESTS} <= {"capability", "connector", "surface"}


def test_unavailable_and_disabled_capabilities_say_so():
    asr = get_manifest("speech_recognition")
    assert asr.status == "unavailable" and not asr.servable and not asr.implemented
    assert asr.reason == "speech_asr_unavailable"
    web = get_manifest("web_retrieval")
    assert web.status == "disabled" and web.external_service == "public_web"
    assert get_manifest("memory").implemented and get_manifest("memory").servable
    for connector in [m for m in MANIFESTS if m.kind == "connector"]:
        assert connector.status == "disabled"          # no external connector is enabled


def test_planning_and_reminder_capabilities_are_declared_available():
    for capability in ("plans", "reminders", "audio", "operations"):
        manifest = get_manifest(capability)
        assert manifest is not None and manifest.status == "available", capability
    assert not get_manifest("plans").scopes == ()
    assert set(get_manifest("plans").scopes) == {manifests.SCOPE_PLANS_READ, manifests.SCOPE_PLANS_WRITE}


def test_manifests_serialise_for_clients():
    payload = manifests_dict()
    assert len(payload) == len(MANIFESTS)
    memory = next(m for m in payload if m["id"] == "memory")
    assert memory["servable"] is True and isinstance(memory["scopes"], list)
    unknown = get_manifest("does-not-exist")
    assert unknown is None
