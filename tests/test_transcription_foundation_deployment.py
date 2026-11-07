"""Bounded receipts and private profile changes fail closed without calling services."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import transcription_deployment as remote  # noqa: E402
import transcription_profile as profile  # noqa: E402
import transcription_qualification as qualification  # noqa: E402
from test_transcription_deployment import artifact as artifact  # noqa: E402
from test_transcription_deployment import deploy_mocks

from omi_local.configuration import Configuration
from omi_local.settings import Settings


def foundation_receipt():
    producer = {
        "model_sha256": "a" * 64, "snapshot": "large-v3-r1", "runtime": "whisper.cpp",
        "runtime_commit": "1234567890ab", "homecompute_revision": "c" * 40,
        "image_id": "sha256:" + "d" * 64, "device": "cuda",
    }
    checks = remote.QUALIFICATION_CHECKS | qualification.FOUNDATION_CHECKS
    return {
        "schema_version": 2, "status": "qualified_transcription_foundation",
        "scope": "bounded_transcription_profile",
        "accuracy_status": "DECLARED_FIXTURES_AND_PROFILE_ONLY",
        "source_revision": producer["homecompute_revision"], "image_id": producer["image_id"],
        "producer": producer, "checks": dict.fromkeys(checks, "passed"),
        "evidence_sha256": dict.fromkeys(checks, "f" * 64),
        "consumer_client_revision": "b" * 40,
        "qualified_asr_hashes": dict.fromkeys((
            "transcription", "audio", "stt_result", "stt_producer", "transcription_filter",
            "recording_transcription_client", "speech_processing", "evaluate_audio",
        ), "a" * 64),
        "deployment_module_hashes": dict.fromkeys(remote.MODULES, "a" * 64),
        "profile": qualification.FIXED_PROFILE | {
            "stt_base_url": "https://gateway.local/recording-stt/v1",
            "recording_transcription_base_url": "https://gateway.local/recording-stt/v1",
        },
        "credential_policy": "reuse_existing_recording_role_key",
        "settings_target": {"relative_path": "archive/.private-settings.json", "sha256": "e" * 64},
        "bounds": {"max_audio_seconds": 1200, "max_audio_bytes": 40_000_000,
                   "backend_timeout_seconds": 600},
        "reserved_reference_exposure": "preparation_exposed_before_first_asr",
        "accuracy_limitations": [
            "Bounded tested recordings only; no universal accuracy or production network claim.",
            "Reserved references were preparation-exposed before their first ASR use.",
            "Existing 120-second archive cuts can change proper names without omitting a reading.",
        ],
    }


@pytest.mark.parametrize("gate", sorted(remote.QUALIFICATION_CHECKS
                                       | qualification.FOUNDATION_CHECKS))
def test_any_pending_foundation_gate_refuses_before_baseline(gate, monkeypatch):
    receipt = foundation_receipt()
    receipt["checks"][gate] = "pending"
    monkeypatch.setattr(remote, "baseline", lambda _: pytest.fail("Unexpected baseline read"))
    with pytest.raises(ValueError, match="qualification"):
        remote.stage({"qualification": receipt})


def test_complete_bounded_foundation_receipt_keeps_its_exact_scope():
    receipt = foundation_receipt()
    normalized = remote.validate_qualification(receipt)
    assert normalized == receipt
    assert normalized["accuracy_status"] == "DECLARED_FIXTURES_AND_PROFILE_ONLY"
    with pytest.raises(ValueError, match="digest"):
        remote.validate_qualification(receipt, require_digest=True)


@pytest.mark.parametrize("case", [
    "missing_device", "bad_source_pin", "missing_evidence", "universal_accuracy", "gain12",
    "false_as_numeric_zero", "language_route", "different_normal_url", "credential_in_url",
    "credentials_in_profile", "escaping_settings_path", "unbounded_audio", "no_limitations",
    "missing_reference_exposure",
])
def test_unqualified_profile_identity_and_scope_are_refused(case):
    receipt = foundation_receipt()
    if case == "missing_device":
        del receipt["producer"]["device"]
    elif case == "bad_source_pin":
        receipt["producer"]["homecompute_revision"] = "latest"
    elif case == "missing_evidence":
        receipt["evidence_sha256"].pop("consumer_profile")
    elif case == "universal_accuracy":
        receipt["accuracy_status"] = "UNIVERSALLY_ACCURATE"
    elif case == "gain12":
        receipt["profile"]["stt_max_gain_db"] = 12
    elif case == "false_as_numeric_zero":
        receipt["profile"]["stt_max_gain_db"] = False
    elif case == "language_route":
        receipt["profile"]["stt_routes"] = {"da": {"model": "other"}}
    elif case == "different_normal_url":
        receipt["profile"]["stt_base_url"] = "https://other.local/v1"
    elif case == "credential_in_url":
        for name in ("stt_base_url", "recording_transcription_base_url"):
            receipt["profile"][name] = "https://user:synthetic@gateway.local/recording-stt/v1"
    elif case == "credentials_in_profile":
        receipt["profile"]["stt_api_key"] = "synthetic-key"
    elif case == "escaping_settings_path":
        receipt["settings_target"]["relative_path"] = "../.private-settings.json"
    elif case == "unbounded_audio":
        receipt["bounds"]["max_audio_seconds"] = 7200
    elif case == "missing_reference_exposure":
        del receipt["reserved_reference_exposure"]
    else:
        receipt["accuracy_limitations"] = []
    with pytest.raises(ValueError):
        remote.validate_qualification(receipt)


@pytest.fixture
def private_profile(tmp_path):
    data, release, saved = (tmp_path / name for name in ("data", "release", "saved"))
    (data / "archive").mkdir(parents=True)
    release.mkdir()
    saved.mkdir()
    target = data / "archive/.private-settings.json"
    original = {"version": 1, "overrides": {
        "recording_transcription_base_url": "https://gateway.local/recording-stt/v1",
        "recording_transcription_api_key": "synthetic-existing-role-key",
        "stt_api_key": "synthetic-older-key", "stt_max_gain_db": 12,
        "recording_transcription_expected_model_snapshot": "obsolete-pin",
        "recap_language": "da", "llm_api_key": "synthetic-unrelated-key",
    }}
    raw = (json.dumps(original) + "\n").encode()
    target.write_bytes(raw)
    target.chmod(0o600)
    receipt = foundation_receipt()
    receipt["settings_target"]["sha256"] = hashlib.sha256(raw).hexdigest()
    info = {"Config": {"Env": ["OMI_DATA_DIR=/data/archive"]},
            "Mounts": [{"Source": str(data), "Destination": "/data"}]}
    config = {"services": {"backend": {"environment": {"KEEP": "untouched"}}}}
    return data, release, saved, target, raw, receipt, info, config


def test_profile_staging_reuses_private_role_key_and_preserves_unrelated_settings(private_profile):
    data, release, _, target, raw, receipt, info, config = private_profile
    change = profile.stage_profile(receipt, config, release, info, data)
    assert target.read_bytes() == raw
    staged = json.loads((release / ".transcription-profile/settings.json").read_bytes())
    assert staged["overrides"]["stt_api_key"] == "synthetic-existing-role-key"
    assert staged["overrides"]["llm_api_key"] == "synthetic-unrelated-key"
    assert staged["overrides"]["recap_language"] == "da"
    assert not set(qualification.PIN_SETTINGS.values()) & staged["overrides"].keys()
    environment = config["services"]["backend"]["environment"]
    assert environment["KEEP"] == "untouched"
    assert json.loads(environment["OMI_STT_EXPECTED_PRODUCER"]) == receipt["producer"]
    assert environment["OMI_RECORDING_TRANSCRIPTION_EXPECTED_DEVICE"] == "cuda"
    assert environment["OMI_STT_MAX_GAIN_DB"] == "0"
    assert "synthetic-existing-role-key" not in json.dumps(change | config)
    assert (release / ".transcription-profile/settings.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("case", ["settings_changed", "wrong_mount", "new_gateway", "missing_key"])
def test_profile_refuses_changed_target_or_role_endpoint(private_profile, case):
    data, release, _, target, raw, receipt, info, config = private_profile
    if case == "settings_changed":
        target.write_bytes(raw + b" ")
    elif case == "wrong_mount":
        info["Mounts"][0]["Source"] = str(data / "other")
    elif case == "new_gateway":
        for key in ("stt_base_url", "recording_transcription_base_url"):
            receipt["profile"][key] = "https://new.local/recording-stt/v1"
    else:
        value = json.loads(raw)
        del value["overrides"]["recording_transcription_api_key"]
        target.write_text(json.dumps(value))
        receipt["settings_target"]["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError):
        profile.stage_profile(receipt, config, release, info, data)


def test_private_profile_install_and_rollback_restore_exact_original_bytes(private_profile):
    data, release, saved, target, raw, receipt, info, config = private_profile
    change = profile.stage_profile(receipt, config, release, info, data)
    (saved / "archive").mkdir()
    shutil.copy2(target, saved / change["relative_path"])
    profile.apply_profile(data, release, saved, change)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == change["after_sha256"]
    profile.restore_profile(data, saved, change)
    assert target.read_bytes() == raw
    assert target.stat().st_mode & 0o777 == 0o600


def test_profile_cas_preserves_newer_edits_before_apply_and_during_rollback(private_profile):
    data, release, saved, target, raw, receipt, info, config = private_profile
    change = profile.stage_profile(receipt, config, release, info, data)
    (saved / "archive").mkdir()
    shutil.copy2(target, saved / change["relative_path"])
    target.write_bytes(raw + b" ")
    with pytest.raises(RuntimeError, match="CAS"):
        profile.apply_profile(data, release, saved, change)
    assert target.read_bytes() == raw + b" "
    target.write_bytes(raw)
    profile.apply_profile(data, release, saved, change)
    newer = target.read_bytes() + b" "
    target.write_bytes(newer)
    with pytest.raises(RuntimeError, match="preserve newer"):
        profile.restore_profile(data, saved, change)
    assert target.read_bytes() == newer


def test_all_producer_pins_are_operator_policy_and_saved_overrides_cannot_shadow(tmp_path):
    receipt = foundation_receipt()
    pin_values = {setting: receipt["producer"][name]
                  for name, setting in qualification.PIN_SETTINGS.items()}
    initial = Settings(data_dir=tmp_path, _env_file=None,
                       stt_expected_producer=receipt["producer"], **pin_values)
    path = tmp_path / ".private-settings.json"
    path.write_text(json.dumps({"overrides": {
        **dict.fromkeys(pin_values, "obsolete-invalid-pin"),
        "stt_expected_producer": {"bad": "obsolete"}, "recap_language": "da",
    }}))
    configuration = Configuration(initial)
    active = configuration.startup(initial)
    assert active.stt_expected_producer == receipt["producer"]
    assert all(getattr(active, name) == value for name, value in pin_values.items())
    view = configuration.view(active)
    assert not set(pin_values) & view["values"].keys()
    assert not set(pin_values) & {field["key"] for field in view["fields"]}
    assert "stt_expected_producer" not in view["values"]
    assert active.recap_language == "da"


def test_foundation_source_change_fails_before_any_baseline_action(monkeypatch):
    receipt = foundation_receipt() | {"receipt_sha256": "f" * 64}
    monkeypatch.setattr(remote, "baseline", lambda _: pytest.fail("Unexpected baseline read"))
    with pytest.raises(ValueError, match="deployment source"):
        remote.stage({"qualification": receipt, "file_hashes": {"wrong": "a" * 64}})


def test_compose_preserves_release_environment_and_assistant_overlay(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/compose.assistant-context.yaml").write_text("services: {}")
    calls = []
    monkeypatch.setattr(remote, "run", lambda *args, **kwargs: calls.append(args))
    remote.compose(tmp_path, "up", "-d", "backend", image="sha256:synthetic")
    command = calls[0]
    assert command[command.index("--env-file") + 1] == str(tmp_path / ".env")
    assert str(tmp_path / "config/compose.assistant-context.yaml") in command


def test_rollback_refuses_changed_release_context_without_overwriting_it(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / ".env").write_bytes(b"original private environment")
    manifest = {"env_sha256": hashlib.sha256((tmp_path / ".env").read_bytes()).hexdigest()}
    (tmp_path / ".env").write_bytes(b"newer private environment")
    with pytest.raises(RuntimeError, match="preserve newer"):
        profile.restore_profile(tmp_path, tmp_path, None, context=(tmp_path, manifest))
    assert (tmp_path / ".env").read_bytes() == b"newer private environment"


@pytest.mark.parametrize("fail_candidate", [False, True])
def test_foundation_restart_verifies_profile_or_restores_settings_before_base_start(
    artifact, private_profile, monkeypatch, fail_candidate,
):
    payload, base, release, calls = artifact
    data, _, _, target, raw, receipt, info, config = private_profile
    manifest_path = release / "config/transcription-overlay.json"
    manifest = json.loads(manifest_path.read_bytes())
    receipt["deployment_module_hashes"] = payload["file_hashes"]
    for name in receipt["qualified_asr_hashes"].keys() & payload["file_hashes"].keys():
        receipt["qualified_asr_hashes"][name] = payload["file_hashes"][name]
    payload["qualification"] = receipt | {"receipt_sha256": "f" * 64}
    manifest["qualification"] = payload["qualification"]
    manifest["settings_change"] = profile.stage_profile(receipt, config, release, info, data)
    manifest_path.write_text(json.dumps(manifest))
    deploy_mocks(monkeypatch, calls)
    monkeypatch.setattr(remote, "DATA", data)

    def backup(destination):
        (destination / "archive").mkdir()
        shutil.copy2(target, destination / manifest["settings_change"]["relative_path"])
        return {manifest["settings_change"]["relative_path"]: hashlib.sha256(raw).hexdigest()}

    monkeypatch.setattr(remote, "backup", backup)

    def ready(image, _hashes):
        if image != payload["base_image"]:
            assert hashlib.sha256(target.read_bytes()).hexdigest() == (
                manifest["settings_change"]["after_sha256"]
            )
            if fail_candidate:
                raise RuntimeError("Synthetic candidate failure")
            return
        assert target.read_bytes() == raw

    monkeypatch.setattr(remote, "ready", ready)
    verified = []
    monkeypatch.setattr(remote, "verify_profile", lambda *args: verified.append(True))
    if fail_candidate:
        with pytest.raises(RuntimeError, match="Synthetic candidate failure"):
            remote.deploy(payload)
        assert target.read_bytes() == raw and not verified
        assert calls[-1][0] == "compose" and calls[-1][1] == base
    else:
        result = remote.deploy(payload)
        assert result["status"] == "deployed" and verified == [True]
        assert json.loads(Path(result["receipt"]).read_text())["profile_verified"] is True


@pytest.mark.parametrize("wrong_actual_pin", [False, True])
def test_profile_readiness_executes_actual_settings_resolution_without_inference(
    private_profile, wrong_actual_pin,
):
    data, release, saved, target, _, receipt, info, config = private_profile
    change = profile.stage_profile(receipt, config, release, info, data)
    (saved / "archive").mkdir()
    shutil.copy2(target, saved / change["relative_path"])
    profile.apply_profile(data, release, saved, change)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("OMI_")}
    environment.update(config["services"]["backend"]["environment"])
    environment["OMI_DATA_DIR"] = str(target.parent)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    if wrong_actual_pin:
        environment["OMI_RECORDING_TRANSCRIPTION_EXPECTED_DEVICE"] = "cpu"

    def local_only_output(*args):
        assert args[:4] == ("docker", "exec", "test-container", "python")
        return subprocess.check_output([sys.executable, *args[4:]], env=environment,
                                       stderr=subprocess.PIPE, text=True)

    if wrong_actual_pin:
        with pytest.raises(subprocess.CalledProcessError):
            profile.verify_profile(receipt, local_only_output, "test-container")
    else:
        profile.verify_profile(receipt, local_only_output, "test-container")
