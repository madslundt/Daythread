"""Pinned overlays fail closed before mutation and retain rollback/data evidence."""

import base64
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import deploy_transcription_homecore as cli  # noqa: E402
import transcription_deployment as remote  # noqa: E402


def reliability_receipt():
    return {
        "status": "qualified_reliability", "scope": "request_determinism",
        "accuracy_status": "NOT_QUALIFIED_FOR_FULL_LONG_COVERAGE_OR_LANGUAGE_RETENTION",
        "source_revision": "a" * 40, "image_id": "sha256:" + "b" * 64,
        "checks": dict.fromkeys(("short_exact_repeat", "long_exact_repeat",
                                "cross_history_repeat", "request_provenance", "request_bounds",
                                "admission_and_idle", "resource_headroom",
                                "full_recording_requests", "complete_input_frame_submission",
                                "segment_timing_and_anchors", "client_response_compatibility"),
                               "passed"),
        "accuracy_limitations": ["Existing omissions and language conversion remain unresolved."],
    }


def test_qualification_requires_completed_identified_runtime(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"status": "pending"}))
    with pytest.raises(ValueError, match="qualification"):
        cli.qualification(path)
    value = reliability_receipt()
    path.write_text(json.dumps(value))
    assert cli.qualification(path) == value | {
        "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


@pytest.mark.parametrize("change", [
    {"status": "qualified"}, {"scope": "transcription_accuracy"},
    {"accuracy_status": "qualified"}, {"accuracy_limitations": []},
    {"accuracy_limitations": [123]}, {"checks": {}},
    {"checks": {"short_exact_repeat": "passed"}}, {"source_revision": None},
])
def test_incomplete_or_unscoped_qualification_is_rejected(tmp_path, change):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(reliability_receipt() | change))
    with pytest.raises(ValueError, match="qualification"):
        cli.qualification(path)


@pytest.mark.parametrize("check", sorted(remote.QUALIFICATION_CHECKS))
def test_failed_required_check_cannot_qualify(tmp_path, check):
    value = reliability_receipt()
    value["checks"][check] = "failed"
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="qualification"):
        cli.qualification(path)


@pytest.mark.parametrize("state", ["queued", "running", "processing", "transcribing",
                                  "summarizing", "pending", "working", "sending", "leased"])
def test_durable_active_or_waiting_work_is_not_cancelled(state):
    with pytest.raises(RuntimeError, match="recording_transcription_jobs"):
        remote.require_restartable({
            "archive.sqlite3": {"recording_transcription_jobs": {state: 1}},
        })


def test_completed_and_blocked_work_is_preserved():
    snapshot = {"archive.sqlite3": {"recording_transcription_jobs": {"blocked": 5, "ready": 3}}}
    remote.require_restartable(snapshot)
    assert snapshot["archive.sqlite3"]["recording_transcription_jobs"] == {"blocked": 5, "ready": 3}


def test_baseline_rejects_image_change_before_reading_installed_source(tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "ROOT", tmp_path)
    base = tmp_path / "releases" / "base"
    base.mkdir(parents=True)
    (tmp_path / "current").symlink_to(base)
    monkeypatch.setattr(remote, "inspect", lambda: {
        "Image": "wrong", "Config": {"Image": "omi-local:base"}, "State": {"Status": "running"},
    })
    monkeypatch.setattr(remote, "installed", lambda _: pytest.fail("Unexpected source read"))
    with pytest.raises(RuntimeError, match="release/image changed"):
        remote.baseline({"base_release": "base", "base_image": "expected"})


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "ROOT", tmp_path)
    base = tmp_path / "releases" / "base"
    (base / "config").mkdir(parents=True)
    (base / "src/omi_local").mkdir(parents=True)
    (base / "config/compose.release.json").write_text(json.dumps({
        "services": {"backend": {"environment": {"KEEP": "yes"}, "image": "old-tag"}},
    }))
    (tmp_path / "current").symlink_to(base)
    (tmp_path / "backups").mkdir()
    raw = b"# reviewed source\n"
    hashes = {name: hashlib.sha256(raw).hexdigest() for name in remote.MODULES}
    payload = {
        "base_release": "base", "base_image": "sha256:" + "b" * 64,
        "baseline_hashes": {name: "old" for name in remote.MODULES},
        "release": "new", "file_hashes": hashes, "helper_hashes": {"helper": "value"},
        "overlay_sha256": "a" * 64, "source_revision": "c" * 40,
        "files": {name: base64.b64encode(raw).decode() for name in remote.MODULES},
        "qualification": reliability_receipt() | {"receipt_sha256": "f" * 64},
    }
    monkeypatch.setattr(remote, "baseline", lambda _: base)
    monkeypatch.setattr(remote, "installed", lambda _: hashes)
    monkeypatch.setattr(remote, "output", lambda *args: (
        "sha256:" + "d" * 64 if args[:3] == ("docker", "image", "inspect") else '{"passed":true}'
    ))
    calls = []
    monkeypatch.setattr(remote, "run", lambda *args, **kwargs: calls.append(args))
    remote.stage(payload)
    return payload, base, tmp_path / "releases/new", calls


def test_stage_uses_immutable_parent_and_only_reviewed_modules(artifact):
    payload, base, release, calls = artifact
    context = release / ".transcription-build"
    dockerfile = (context / "Dockerfile").read_text()
    assert dockerfile.startswith("FROM " + payload["base_image"] + "\n")
    expected_files = {n + ".py" for n in remote.MODULES} | {"Dockerfile"}
    assert {p.name for p in context.iterdir()} == expected_files
    assert dockerfile.count("COPY ") == len(remote.MODULES)
    assert calls[0][:4] == ("docker", "build", "--network=none", "--pull=false")
    original = json.loads((base / "config/compose.release.json").read_text())
    staged = json.loads((release / "config/compose.release.json").read_text())
    assert original["services"]["backend"]["image"] == "old-tag"
    assert staged["services"]["backend"]["environment"] == {"KEEP": "yes"}
    assert staged["services"]["backend"]["image"] == "sha256:" + "d" * 64
    manifest = json.loads((release / "config/transcription-overlay.json").read_text())
    assert manifest["qualification"] == payload["qualification"]
    assert (release.parent.parent / "current").resolve() == base


def test_overlay_packages_producer_validation_and_both_cache_contracts():
    root = Path(__file__).resolve().parents[1]
    payload = cli.payload(root, "plan")
    assert set(payload["files"]) == remote.MODULES == set(cli.BASE_HASHES)
    for name in ("source_matching", "recording_transcription_store", "stt_producer"):
        raw = (root / "src/omi_local" / (name + ".py")).read_bytes()
        assert base64.b64decode(payload["files"][name]) == raw
        assert payload["file_hashes"][name] == hashlib.sha256(raw).hexdigest()


def test_installed_hashes_distinguish_absent_baseline_module(tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "SITE", str(tmp_path))
    raw = b"# installed legacy module\n"
    (tmp_path / "source_matching.py").write_bytes(raw)
    monkeypatch.setattr(remote, "output", lambda *args: subprocess.check_output(
        [sys.executable, "-c", args[-1]], text=True).strip())
    observed = remote.installed(["local-test"])
    assert set(observed) == remote.MODULES
    assert observed["source_matching"] == hashlib.sha256(raw).hexdigest()
    assert observed["stt_producer"] is None
    (tmp_path / "stt_producer.py").write_bytes(b"# unexpected existing helper\n")
    assert remote.installed(["local-test"])["stt_producer"] is not None


def test_stage_rejects_generic_qualification_before_baseline(monkeypatch):
    monkeypatch.setattr(remote, "baseline", lambda _: pytest.fail("Unexpected baseline access"))
    with pytest.raises(ValueError, match="qualification"):
        remote.stage({"qualification": {"status": "qualified"}})


def test_deploy_rejects_changed_qualification_before_stopping(artifact):
    payload, _, _, calls = artifact
    payload["qualification"] = payload["qualification"] | {"receipt_sha256": "e" * 64}
    with pytest.raises(RuntimeError, match="Staged artifact differs"):
        remote.deploy(payload)
    assert not any(call[:2] == ("docker", "stop") for call in calls)


def deploy_mocks(monkeypatch, calls):
    monkeypatch.setattr(remote, "queue_snapshot", lambda: {"omi.sqlite3": {"audio": {"done": 1}}})
    monkeypatch.setattr(remote, "admission", lambda: {"active_requests": 0})
    monkeypatch.setattr(remote, "inspect", lambda: {
        "Id": "old", "Image": "sha256:" + "b" * 64,
        "State": {"Running": False, "ExitCode": 0, "OOMKilled": False},
    })
    monkeypatch.setattr(remote, "compose", lambda path, *args, **kwargs: calls.append(
        ("compose", path, args, kwargs)))

    def backup(path):
        path.mkdir(mode=0o700, exist_ok=True)
        calls.append(("backup", path))
        return {"omi.sqlite3": "database-hash"}

    monkeypatch.setattr(remote, "backup", backup)


def test_failed_health_rolls_back_exact_image_without_restoring_database(artifact, monkeypatch):
    payload, base, release, calls = artifact
    deploy_mocks(monkeypatch, calls)

    def ready(image, hashes):
        calls.append(("ready", image))
        if image != payload["base_image"]:
            raise RuntimeError("Failed candidate health")

    monkeypatch.setattr(remote, "ready", ready)
    with pytest.raises(RuntimeError, match="Failed candidate health"):
        remote.deploy(payload)
    receipt = json.loads((release.parent.parent / "backups/new/deployment.json").read_text())
    assert receipt["status"] == "rolled_back"
    assert receipt["backup_hashes"] == {"omi.sqlite3": "database-hash"}
    assert (release.parent.parent / "current").resolve() == base
    rollback = [call for call in calls if call[0] == "compose"][-1]
    assert rollback[1] == base and rollback[3] == {"image": payload["base_image"]}


def test_success_switches_after_health_and_retains_rollback_receipt(artifact, monkeypatch):
    payload, base, release, calls = artifact
    deploy_mocks(monkeypatch, calls)
    monkeypatch.setattr(remote, "admission", lambda: {"active_requests": 2})

    def ready(image, hashes):
        assert (release.parent.parent / "current").resolve() == base
        calls.append(("ready", image))

    monkeypatch.setattr(remote, "ready", ready)
    result = remote.deploy(payload)
    receipt = json.loads(Path(result["receipt"]).read_text())
    assert result["status"] == "deployed"
    assert (release.parent.parent / "current").resolve() == release
    assert (release.parent.parent / "previous.transcription").resolve() == base
    assert receipt["qualification"] == payload["qualification"]
    assert receipt["stopped_jobs"] == receipt["before_jobs"] == receipt["after_jobs"]
    assert receipt["gateway_admission"]["active_requests"] == 2


def test_interrupted_stop_has_durable_receipt_then_recovers_exact_image(artifact, monkeypatch):
    payload, base, release, calls = artifact
    deploy_mocks(monkeypatch, calls)
    monkeypatch.setattr(remote, "ready", lambda *_: None)
    path = release.parent.parent / "backups/new/deployment.json"

    def interrupted(*args, **kwargs):
        assert json.loads(path.read_text())["phase"] == "stopping"
        raise InterruptedError("simulated SSH loss")

    monkeypatch.setattr(remote, "run", interrupted)
    with pytest.raises(InterruptedError):
        remote.deploy(payload)
    receipt = json.loads(path.read_text())
    assert receipt["status"] == "rolled_back" and receipt["failure"] == "InterruptedError"
    assert (release.parent.parent / "current").resolve() == base
    assert [call for call in calls if call[0] == "compose"][-1][3] == {
        "image": payload["base_image"],
    }


def test_failed_rollback_is_recorded_and_explicit_recover_handles_stopped_backend(
    artifact, monkeypatch,
):
    payload, base, release, calls = artifact
    deploy_mocks(monkeypatch, calls)
    monkeypatch.setattr(remote, "ready", lambda *_: None)

    def interrupted(*args, **kwargs):
        raise InterruptedError("interrupted stop")

    def failed(*args, **kwargs):
        raise RuntimeError("Docker temporarily unavailable")

    monkeypatch.setattr(remote, "run", interrupted)
    monkeypatch.setattr(remote, "compose", failed)
    with pytest.raises(RuntimeError, match="Docker temporarily"):
        remote.deploy(payload)
    path = release.parent.parent / "backups/new/deployment.json"
    receipt = json.loads(path.read_text())
    assert receipt["status"] == "rollback_failed" and receipt["phase"] == "recovery_required"
    assert receipt["failure"] == "InterruptedError" and receipt["rollback_error"] == "RuntimeError"
    monkeypatch.setattr(remote, "compose", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(remote, "baseline", lambda _: pytest.fail("Recovery accepts stopped state"))
    assert remote.recover(payload)["status"] == "rolled_back"
    assert (release.parent.parent / "current").resolve() == base


def test_conflicting_pointer_survives_switch_and_recovery(artifact, monkeypatch):
    payload, base, release, calls = artifact
    deploy_mocks(monkeypatch, calls)
    current = release.parent.parent / "current"
    newer = release.parent / "another-release"
    newer.mkdir()

    def ready(*_):
        current.unlink()
        current.symlink_to(newer)

    monkeypatch.setattr(remote, "ready", ready)
    with pytest.raises(RuntimeError, match="another release"):
        remote.deploy(payload)
    assert current.resolve() == newer
    assert len([call for call in calls if call[0] == "compose"]) == 1
    receipt = json.loads((release.parent.parent / "backups/new/deployment.json").read_text())
    assert receipt["status"] == "rollback_failed"


def test_job_claimed_during_stop_race_prevents_candidate_start(artifact, monkeypatch):
    payload, base, release, calls = artifact
    deploy_mocks(monkeypatch, calls)
    snapshots = iter([{"omi.sqlite3": {}}] * 2 + [
        {"omi.sqlite3": {"recording_transcription_jobs": {"processing": 1}}},
    ])
    monkeypatch.setattr(remote, "queue_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(remote, "ready", lambda *_: None)
    with pytest.raises(RuntimeError, match="Durable work"):
        remote.deploy(payload)
    transitions = [call for call in calls if call[0] == "compose"]
    assert len(transitions) == 1 and transitions[0][1] == base


def test_concurrent_deployment_cannot_enter_mutation_window(tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "ROOT", tmp_path)
    with remote.deployment_lock():
        with pytest.raises(BlockingIOError):
            with remote.deployment_lock():
                pytest.fail("Second deployment entered the mutation window")
    with remote.deployment_lock():
        pass


def test_foreground_shutdown_timeout_never_recreates_or_force_kills(artifact, monkeypatch):
    payload, base, release, calls = artifact
    deploy_mocks(monkeypatch, calls)
    monkeypatch.setattr(remote, "inspect", lambda: {
        "Id": "old", "Image": payload["base_image"], "State": {"Running": True},
    })
    monkeypatch.setattr(remote, "wait_for_shutdown", lambda *_: (_ for _ in ()).throw(
        remote.ShutdownPending("still draining")))
    with pytest.raises(remote.ShutdownPending):
        remote.deploy(payload)
    assert (release.parent.parent / "current").resolve() == base
    assert not [call for call in calls if call[0] == "compose"]
    assert ("docker", "kill", "--signal=SIGTERM", "omi-local-backend-1") in calls
    receipt = json.loads((release.parent.parent / "backups/new/deployment.json").read_text())
    assert receipt["status"] == "shutdown_pending"
    with pytest.raises(remote.ShutdownPending):
        remote.recover(payload)
    assert not [call for call in calls if call[0] == "compose"]


def test_remote_program_bundles_the_reviewed_shutdown_helper():
    root = Path(__file__).resolve().parents[1]
    prior = sys.modules.get("transcription_shutdown")
    try:
        scope = {"__name__": "deployment_review"}
        exec(compile(cli.remote_program(root), "remote_deployment", "exec"), scope)
        assert scope["wait_for_shutdown"].__module__ == "transcription_shutdown"
        assert "scripts/transcription_shutdown.py" in cli.HELPERS
    finally:
        sys.modules["transcription_shutdown"] = prior


@pytest.mark.parametrize("rollback", [False, True])
def test_speaker_identity_and_retry_metadata_retained_on_restart_and_rollback(
    artifact, monkeypatch, tmp_path, rollback,
):
    payload, _, release, calls = artifact
    deploy_mocks(monkeypatch, calls)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(remote, "DATA", data)
    path = data / "omi.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE speaker_jobs(scope_key TEXT PRIMARY KEY,fingerprint TEXT,"
                   "state TEXT,attempts INTEGER,retry_at TEXT,error TEXT,updated_at TEXT)")
        db.execute("INSERT INTO speaker_jobs VALUES('source:a','exact-input','processing',"
                   "5,'old-retry',NULL,'before')")
    monkeypatch.setattr(remote, "queue_snapshot", lambda: {
        "omi.sqlite3": {"speaker_jobs": {"processing": 1}},
    })

    def ready(image, _hashes):
        if rollback and image != payload["base_image"]:
            # A new job can be claimed by the candidate before readiness fails.
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO speaker_jobs VALUES('source:b','new-input',"
                           "'processing',0,NULL,NULL,'during-candidate')")
            raise RuntimeError("Failed candidate health")
        with sqlite3.connect(path) as db:
            db.execute("UPDATE speaker_jobs SET state='error',attempts=7,retry_at='later',"
                       "error='Speaker detection failed',updated_at='after' "
                       "WHERE scope_key='source:a'")

    monkeypatch.setattr(remote, "ready", ready)
    if rollback:
        with pytest.raises(RuntimeError, match="Failed candidate health"):
            remote.deploy(payload)
    else:
        remote.deploy(payload)
    receipt = json.loads((release.parent.parent / "backups/new/deployment.json").read_text())
    before = receipt["speaker_before"]["omi.sqlite3"]["jobs"]["source:a"]
    result_key = "speaker_after_rollback" if rollback else "speaker_after"
    jobs = receipt[result_key]["omi.sqlite3"]["jobs"]
    assert before["attempts"] == 5 and before["retry_at"] == "old-retry"
    assert jobs["source:a"] == {
        "fingerprint": "exact-input", "state": "error", "attempts": 7,
        "retry_at": "later", "error": "Speaker detection failed", "updated_at": "after",
    }
    if rollback:
        assert jobs["source:b"]["fingerprint"] == "new-input"
