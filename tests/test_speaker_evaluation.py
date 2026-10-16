"""Mocked runtime tests validate software, never production recognition accuracy."""

import io
import json
import sqlite3
import wave

import httpx
import pytest

from omi_local.voice_matching import validate_report
from omi_local.voice_store import turn_signature
from scripts.evaluate_speakers import (
    evaluate,
    inspect_audio,
    local_service_url,
    main,
    read_gallery,
)

MODEL = {"id": "fixture/embedding", "revision": "a" * 64,
         "dimension": 2, "normalization": "l2-v1"}


def wav(path, sample, seconds=80, rate=16000, channels=1):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setparams((channels, 2, rate, 0, "NONE", "PCM"))
        audio.writeframes(sample.to_bytes(2, "little", signed=True) * (rate * seconds * channels))
    path.write_bytes(stream.getvalue())
    return path


@pytest.fixture
def dataset(tmp_path):
    archive = tmp_path / "omi.sqlite3"
    with sqlite3.connect(archive) as db:
        db.executescript("""
        CREATE TABLE audio(id TEXT,state TEXT,digest TEXT,path TEXT,audio_available INTEGER);
        CREATE TABLE voice_profiles(id TEXT);
        CREATE TABLE speaker_scopes(scope_key TEXT,fingerprint TEXT,payload TEXT);
        CREATE TABLE voice_examples(id TEXT,profile_id TEXT,scope_key TEXT,embedding TEXT,
            model TEXT,source_ids TEXT,source_digests TEXT,fingerprint TEXT,
            turn_signature TEXT,seconds REAL);
        """)
        for owner, vector in (("mads", [1.0, 0.0]), ("alice", [0.0, 1.0])):
            db.execute("INSERT INTO voice_profiles VALUES(?)", (owner,))
            for index in range(2):
                key = f"{owner}-{index}"
                path = wav(tmp_path / f"gallery-{key}.wav", 100 + index + 10 * (owner == "alice"),
                           seconds=12)
                _, metadata = inspect_audio(path)
                digest = metadata["audio_sha256"]
                db.execute("INSERT INTO audio VALUES(?,?,?,?,1)", (key, "done", digest, str(path)))
                payload = {"turns": [{"source_id": key, "speaker_id": "A", "start": 0, "end": 12}]}
                db.execute("INSERT INTO speaker_scopes VALUES(?,?,?)", (
                    "source:" + key, "fingerprint", json.dumps(payload),
                ))
                db.execute("INSERT INTO voice_examples VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    key, owner, "source:" + key, json.dumps(vector), json.dumps(MODEL),
                    json.dumps([key]), json.dumps([digest]), "fingerprint", turn_signature(payload),
                    12,
                ))
    entries, responses = [], []
    for split, count in (("calibration", 2), ("test", 5)):
        for index in range(count):
            path = wav(tmp_path / f"{split}-{index}.wav", 1000 + index + 10 * (split == "test"))
            labels = {f"known-{i}": "mads" for i in range(6)} | {
                "unknown-0": None, "unknown-1": None,
            }
            entries.append({"path": path.name, "conversation_id": f"{split}-{index}",
                            "split": split, "speakers": labels})
            responses.append({"embedding_model": MODEL, "turns": [
                {"start": i * 10, "end": (i + 1) * 10, "speaker": label}
                for i, label in enumerate(labels)
            ], "voices": [
                {"speaker": label, "embedding": [1.0, 0.0] if identity else [-1.0, 0.0],
                 "clean_seconds": 10, "overlap_ratio": 0, "clipped_ratio": 0}
                for label, identity in labels.items()
            ]})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(entries))
    return archive, manifest, entries, responses


def mock_runtime(responses, callback=None):
    calls = []

    def handle(request):
        assert request.url.path == "/diarize"
        assert b"RIFF" in request.content
        assert b'audio/wav' in request.content
        assert b'"embedding"' not in request.content
        assert b'name="file"' in request.content
        calls.append(request)
        if callback:
            callback(len(calls))
        return httpx.Response(200, json=responses[len(calls) - 1])

    return httpx.Client(transport=httpx.MockTransport(handle)), calls


def test_evaluator_sends_actual_wavs_and_rebuilds_usable_report(dataset):
    archive, manifest, entries, responses = dataset
    client, calls = mock_runtime(responses)
    report = evaluate(archive, manifest, "http://127.0.0.1:8790", client)
    assert len(calls) == len(entries)
    assert report["passed"]
    examples, model, pcm_hashes = read_gallery(archive)
    assert report["gallery_pcm_hashes"] == pcm_hashes
    assert validate_report(report, examples, model)[0]


@pytest.mark.parametrize("kind", ["source_missing", "file_missing", "digest_changed",
                                  "scope_changed", "turns_changed", "expired", "unavailable"])
def test_gallery_never_retains_changed_or_missing_evidence(dataset, kind):
    archive, manifest, entries, _ = dataset
    with sqlite3.connect(archive) as db:
        if kind == "source_missing":
            db.execute("DELETE FROM audio WHERE id='mads-0'")
        elif kind == "file_missing":
            (manifest.parent / "gallery-mads-0.wav").unlink()
        elif kind == "digest_changed":
            (manifest.parent / "gallery-mads-0.wav").write_bytes(
                (manifest.parent / "gallery-mads-1.wav").read_bytes(),
            )
        elif kind == "scope_changed":
            db.execute("UPDATE speaker_scopes SET fingerprint='changed' "
                       "WHERE scope_key='source:mads-0'")
        elif kind == "turns_changed":
            db.execute("UPDATE speaker_scopes SET payload=? WHERE scope_key='source:mads-0'", (
                json.dumps({"turns": []}),
            ))
        elif kind == "expired":
            db.execute("UPDATE audio SET state='expired' WHERE id='mads-0'")
        else:
            db.execute("UPDATE audio SET audio_available=0 WHERE id='mads-0'")
    examples, _, _ = read_gallery(archive)
    assert "mads-0" not in {example["id"] for example in examples}


@pytest.mark.parametrize("kind", ["embedding_shortcut", "conversation", "gallery_audio",
                                  "repeated_audio", "rewrapped_audio"])
def test_leaks_and_embedding_shortcuts_fail_before_any_runtime_request(dataset, kind):
    archive, manifest, entries, responses = dataset
    if kind == "embedding_shortcut":
        entries[0]["embedding"] = [1, 0]
    elif kind == "conversation":
        entries[0]["conversation_id"] = "mads-0"
    elif kind == "gallery_audio":
        entries[0]["path"] = "gallery-mads-0.wav"
    elif kind == "repeated_audio":
        entries[-1]["path"] = entries[0]["path"]
    else:
        source = manifest.parent / entries[0]["path"]
        target = manifest.parent / entries[-1]["path"]
        data = bytearray(source.read_bytes())
        data[38:40] = b"\x01\x00"  # Header change, same PCM: reject by PCM fingerprint.
        target.write_bytes(data)
    manifest.write_text(json.dumps(entries))
    client, calls = mock_runtime(responses)
    with pytest.raises(ValueError):
        evaluate(archive, manifest, "http://127.0.0.1:8790", client)
    assert not calls


def test_model_mismatch_and_incomplete_ground_truth_are_rejected(dataset):
    archive, manifest, entries, responses = dataset
    responses[0]["embedding_model"] = {**MODEL, "revision": "b" * 64}
    client, _ = mock_runtime(responses)
    with pytest.raises(ValueError, match="different embedding model"):
        evaluate(archive, manifest, "http://127.0.0.1:8790", client)
    responses[0]["embedding_model"] = MODEL
    del entries[0]["speakers"]["unknown-0"]
    manifest.write_text(json.dumps(entries))
    client, _ = mock_runtime(responses)
    with pytest.raises(ValueError, match="every runtime speaker"):
        evaluate(archive, manifest, "http://127.0.0.1:8790", client)


def test_gallery_changes_during_inference_abort_report(dataset):
    archive, manifest, _, responses = dataset

    def remove_after_first(call):
        if call == 1:
            with sqlite3.connect(archive) as db:
                db.execute("DELETE FROM voice_examples WHERE id='mads-0'")

    client, _ = mock_runtime(responses, remove_after_first)
    with pytest.raises(ValueError, match="changed during evaluation"):
        evaluate(archive, manifest, "http://127.0.0.1:8790", client)


def test_non_pcm_wrong_rate_stereo_and_truncated_wavs_fail(tmp_path):
    for rate, channels in ((8000, 1), (16000, 2)):
        with pytest.raises(ValueError, match="16 kHz mono"):
            inspect_audio(wav(tmp_path / "bad.wav", 100, 1, rate, channels))
    path = wav(tmp_path / "truncated.wav", 100, 1)
    path.write_bytes(path.read_bytes()[:-100])
    with pytest.raises(ValueError, match="Truncated"):
        inspect_audio(path)


@pytest.mark.parametrize("url", ["https://8.8.8.8", "ftp://127.0.0.1", "http://a:b@127.0.0.1",
                                 "http://127.0.0.1?key=value"])
def test_hosted_or_credentialed_services_are_rejected(url):
    with pytest.raises(ValueError):
        local_service_url(url)


def test_cli_writes_failed_diagnostic_and_cannot_overwrite_archive_or_manifest(dataset, tmp_path):
    archive, manifest, _, _ = dataset
    output = tmp_path / "failed-report.json"
    result = main(["--archive", str(archive), "--manifest", str(tmp_path / "missing.json"),
                   "--service-url", "http://127.0.0.1:8790", "--output", str(output)])
    assert result == 1
    assert json.loads(output.read_text())["passed"] is False
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(SystemExit):
        main(["--archive", str(archive), "--manifest", str(manifest),
              "--service-url", "http://127.0.0.1:8790", "--output", str(manifest)])
