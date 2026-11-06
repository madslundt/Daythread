"""Pinned overlays fail closed before mutation and retain rollback/data evidence."""

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import deploy_transcription_homecore as cli  # noqa: E402
import transcription_deployment as remote  # noqa: E402


def test_qualification_requires_completed_identified_runtime(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"status": "pending"}))
    with pytest.raises(ValueError, match="qualification"):
        cli.qualification(path)
    value = {"status": "qualified", "source_revision": "a" * 40, "image_id": "sha256:" + "b" * 64}
    path.write_text(json.dumps(value))
    assert cli.qualification(path) == value | {
        "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


@pytest.mark.parametrize("admission", [{}, {"active_requests": True}, {"active_requests": 1}])
def test_unknown_or_active_gateway_refuses_deployment(admission):
    with pytest.raises(RuntimeError, match="Gateway"):
        remote.require_idle({"archive.sqlite3": {}}, admission)


@pytest.mark.parametrize("state", sorted(remote.BUSY))
def test_durable_active_or_waiting_work_is_not_cancelled(state):
    with pytest.raises(RuntimeError, match="recording_transcription_jobs"):
        remote.require_idle({"archive.sqlite3": {"recording_transcription_jobs": {state: 1}}},
                            {"active_requests": 0})


def test_completed_and_blocked_work_is_preserved():
    snapshot = {"archive.sqlite3": {"recording_transcription_jobs": {"blocked": 5, "ready": 3}}}
    remote.require_idle(snapshot, {"active_requests": 0})
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
        "qualification": {"status": "qualified"},
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


def test_stage_uses_immutable_parent_and_only_five_modules(artifact):
    payload, base, release, calls = artifact
    context = release / ".transcription-build"
    dockerfile = (context / "Dockerfile").read_text()
    assert dockerfile.startswith("FROM " + payload["base_image"] + "\n")
    expected_files = {n + ".py" for n in remote.MODULES} | {"Dockerfile"}
    assert {p.name for p in context.iterdir()} == expected_files
    assert dockerfile.count("COPY ") == 5
    assert calls[0][:4] == ("docker", "build", "--network=none", "--pull=false")
    original = json.loads((base / "config/compose.release.json").read_text())
    staged = json.loads((release / "config/compose.release.json").read_text())
    assert original["services"]["backend"]["image"] == "old-tag"
    assert staged["services"]["backend"]["environment"] == {"KEEP": "yes"}
    assert staged["services"]["backend"]["image"] == "sha256:" + "d" * 64
    assert (release.parent.parent / "current").resolve() == base


def deploy_mocks(monkeypatch, calls):
    monkeypatch.setattr(remote, "queue_snapshot", lambda: {"omi.sqlite3": {"audio": {"done": 1}}})
    monkeypatch.setattr(remote, "admission", lambda: {"active_requests": 0})
    monkeypatch.setattr(remote, "inspect", lambda: {"State": {"ExitCode": 0, "OOMKilled": False}})
    monkeypatch.setattr(remote, "compose", lambda path, *args, **kwargs: calls.append(
        ("compose", path, args, kwargs)))

    def backup(path):
        path.mkdir(mode=0o700)
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
