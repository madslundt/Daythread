"""Quality metrics and live opt-in behavior using local fixtures and mock STT."""

import io
import json
import os
import subprocess
import sys
import wave
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from scripts.evaluate_audio import aggregate, character_distance, evaluate, metrics, words


def test_corpus_metrics_do_not_let_a_short_case_reverse_the_ranking():
    reference = " ".join(["word"] * 99)
    better = [{"run": metrics("yes", "", [])}, {"run": metrics(reference, reference, [])}]
    worse = [{"run": metrics("yes", "yes", [])}, {"run": metrics(
        reference, " ".join(["wrong"] * 10 + ["word"] * 89), [],
    )}]
    assert aggregate(better, "run")["wer"] == 0.01
    assert aggregate(worse, "run")["wer"] == 0.1
    assert aggregate(better, "run")["macro_wer"] == 0.5
    assert aggregate(better, "run")["deletions"] == 1


def test_character_errors_phrase_counts_and_silent_insertions():
    assert metrics("MØDES på café", "mødes på cafe\u0301", [])["cer"] == 0
    assert metrics("cat", "cut", [])["cer"] == 1 / 3
    rows = [
        {"run": metrics("a b c", "a b c", ["a", "b", "c"])},
        {"run": metrics("d", "", ["d"])},
        {"run": metrics("", "invented words", [])},
    ]
    result = aggregate(rows, "run")
    assert result["required_phrase_recall"] == 0.75
    assert result["wer"] == 0.75
    assert result["insertions"] == result["hallucinated_words"] == 2
    assert result["false_speech"] == 1


@pytest.mark.parametrize(("reference", "hypothesis", "distance"), [
    ("", "abc", 3), ("abc", "", 3), ("kitten", "sitting", 3),
    ("morgenmad", "morgen mad", 1), ("ab", "ba", 2), ("abc", "axc", 1),
    ("a" * 10000, "a" * 9999 + "b", 1), ("a" * 10000, "b" * 10000, 10000),
])
def test_character_distance_is_exact_and_handles_long_strings(reference, hypothesis, distance):
    assert character_distance(reference, hypothesis) == distance


def test_word_alignment_work_is_bounded():
    with pytest.raises(ValueError, match="work limit"):
        metrics("a " * 2001, "b " * 2000, [])


@pytest.fixture
def offline_files(tmp_path):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\x01\0" * 16000)
    data = output.getvalue()
    (tmp_path / "fixture.wav").write_bytes(data)
    manifest = tmp_path / "cases.json"
    cases = [{
        "id": "mixed", "audio": "fixture.wav", "category": "quiet", "language": "da-en",
        "reference": "Mads sender the draft fredag", "required_phrases": ["Mads", "fredag"],
        "reference_provenance": {"kind": "human", "source": "human-reviewed", "revision": "v1"},
    }, {
        "id": "noise", "audio": "fixture.wav", "category": "noise_only", "language": "none",
        "reference": "", "reference_provenance": {
            "kind": "human", "source": "human-reviewed", "revision": "v1",
        },
    }]
    manifest.write_text(json.dumps(cases))
    hypotheses = tmp_path / "runs.json"
    runs = {"schema_version": 1, "runs": [{
        "id": run_id,
        "metadata": {"provider": run_id, "model": "undisclosed", "revision": "unknown",
                     "configuration": "raw-v1"},
        "cases": [{"id": case["id"], "audio_sha256": sha256(data).hexdigest(),
                   "text": case["reference"], "latency_ms": 200}
                  for case in cases],
    } for run_id in ("daythread", "plaud")]}
    hypotheses.write_text(json.dumps(runs))
    return manifest, hypotheses, cases, runs


def test_offline_comparison_scores_identical_audio_without_network_or_speech_output(
    offline_files, monkeypatch,
):
    from scripts.evaluate_audio import evaluate_offline

    def forbidden(*args, **kwargs):
        pytest.fail("Offline scoring must not construct inference, settings or HTTP clients")

    monkeypatch.setattr("scripts.evaluate_audio.Inference", forbidden)
    monkeypatch.setattr("scripts.evaluate_audio.Settings", forbidden)
    monkeypatch.setattr("scripts.evaluate_audio.httpx.AsyncClient", forbidden)
    manifest, hypotheses, cases, runs = offline_files
    runs["runs"][0]["cases"][0]["text"] = "Mads sender the draft"
    hypotheses.write_text(json.dumps(runs))
    report = evaluate_offline(manifest, hypotheses)
    assert report["reference_kind"] == "human"
    assert report["comparison"] == "human_reference_accuracy"
    assert report["cases"][0]["reference_context"] == "same_audio"
    assert report["groups"]["language:da-en"]["daythread"]["wer"] == 0.2
    assert report["groups"]["overall"]["plaud"]["wer"] == 0
    assert report["groups"]["overall"]["daythread"]["scored"] == 2
    assert report["cases"][0]["latency"]["daythread"]["real_time_factor"] == 0.2
    assert report["cases"][0]["audio_sha256"] == runs["runs"][0]["cases"][0]["audio_sha256"]
    assert report["manifest_sha256"] == sha256(manifest.read_bytes()).hexdigest()
    serialized = json.dumps(report)
    assert cases[0]["reference"] not in serialized
    assert "fixture.wav" not in serialized
    assert "reference_sha256" in serialized


@pytest.mark.parametrize("damage", [
    "missing_case", "extra_case", "duplicate_case", "duplicate_run", "wrong_hash",
    "missing_reference_provenance", "mixed_reference_kinds", "secret_metadata", "invalid_latency",
    "reserved_run_id", "metadata_url", "metadata_secret", "duplicate_fixture", "invalid_context",
])
def test_offline_comparison_rejects_unpaired_or_unverifiable_inputs(offline_files, damage):
    from scripts.evaluate_audio import evaluate_offline

    manifest, hypotheses, cases, runs = offline_files
    first = runs["runs"][0]
    if damage == "missing_case":
        first["cases"].pop()
    elif damage == "extra_case":
        first["cases"].append({**first["cases"][0], "id": "unrelated"})
    elif damage == "duplicate_case":
        first["cases"].append(first["cases"][0])
    elif damage == "duplicate_run":
        runs["runs"].append(first)
    elif damage == "wrong_hash":
        first["cases"][0]["audio_sha256"] = "0" * 64
    elif damage == "missing_reference_provenance":
        cases[0].pop("reference_provenance")
    elif damage == "mixed_reference_kinds":
        cases[0]["reference_provenance"]["kind"] = "vendor"
    elif damage == "secret_metadata":
        first["metadata"]["api_key"] = "private-secret"
    elif damage == "invalid_latency":
        first["cases"][0]["latency_ms"] = float("nan")
    elif damage == "reserved_run_id":
        first["id"] = "latency"
    elif damage == "metadata_url":
        first["metadata"]["configuration"] = "https://example.com?token=secret"
    elif damage == "metadata_secret":
        first["metadata"]["configuration"] = "sk-private-token"
    elif damage == "duplicate_fixture":
        cases.append(cases[0])
    elif damage == "invalid_context":
        cases[0]["reference_context"] = "assumed-equivalent"
    manifest.write_text(json.dumps(cases))
    hypotheses.write_text(json.dumps(runs))
    with pytest.raises(ValueError):
        evaluate_offline(manifest, hypotheses)


def test_vendor_reference_is_labeled_as_agreement_not_accuracy(offline_files):
    from scripts.evaluate_audio import evaluate_offline

    manifest, hypotheses, cases, runs = offline_files
    for case in cases:
        case["reference_provenance"].update(kind="vendor", source="plaud-raw")
    manifest.write_text(json.dumps(cases))
    report = evaluate_offline(manifest, hypotheses)
    assert report["comparison"] == "vendor_reference_agreement"
    assert "not an accuracy benchmark" in report["notice"]


def test_unknown_audio_category_and_full_context_reference_are_disclosed(offline_files):
    from scripts.evaluate_audio import evaluate_offline

    manifest, hypotheses, cases, _ = offline_files
    cases[0].update(category="unknown", reference_context="full_recording_excerpt")
    for case in cases:
        case["reference_provenance"].update(kind="vendor", source="plaud-raw")
    manifest.write_text(json.dumps(cases))
    report = evaluate_offline(manifest, hypotheses)
    assert report["groups"]["category:unknown"]["daythread"]["scored"] == 1
    assert report["cases"][0]["reference_context"] == "full_recording_excerpt"
    assert report["reference_context_counts"] == {"same_audio": 1, "full_recording_excerpt": 1}
    assert "did not receive the same cropped audio" in report["notice"]


def test_offline_comparison_bounds_total_alignment_work(offline_files, monkeypatch):
    from scripts.evaluate_audio import evaluate_offline

    manifest, hypotheses, _, _ = offline_files
    monkeypatch.setattr("scripts.evaluate_audio.OFFLINE_ALIGNMENT_CELL_BUDGET", 1)
    with pytest.raises(ValueError, match="total word alignment work limit"):
        evaluate_offline(manifest, hypotheses)


def test_offline_cli_needs_no_live_mode_and_keeps_credentials_out_of_report(offline_files):
    manifest, hypotheses, _, _ = offline_files
    result = subprocess.run([
        sys.executable, "scripts/evaluate_audio.py", str(manifest),
        "--hypotheses", str(hypotheses),
    ], cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True,
        timeout=10, env={**os.environ, "OMI_STT_BASE_URL": "http://127.0.0.1:1",
                         "OMI_STT_API_KEY": "private-evaluation-secret"})
    assert json.loads(result.stdout)["mode"] == "offline"
    assert "private-evaluation-secret" not in result.stdout + result.stderr


def test_offline_cli_cannot_be_combined_with_live_mode(offline_files):
    manifest, hypotheses, _, _ = offline_files
    result = subprocess.run([
        sys.executable, "scripts/evaluate_audio.py", str(manifest),
        "--hypotheses", str(hypotheses), "--live",
    ], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2


def test_wer_tracks_omissions_and_negative_false_speech():
    result = metrics("May will call on Friday", "May calls on Friday", ["May", "Friday"])
    assert result["wer"] == 2 / 5
    assert result["deletion_rate"] == 1 / 5
    assert result["required_phrase_recall"] == 1
    assert metrics("", "Imagined speech", [])["false_speech"] is True
    assert metrics("", "", [])["false_speech"] is False
    assert words("MØDES på FREDAG!") == ["mødes", "på", "fredag"]
    assert metrics("on Monday", "Mondayish", ["Monday"])["required_phrase_recall"] == 0


def test_unavailable_cleanup_is_not_scored_as_a_quality_success():
    result = aggregate([{"cleanup": None}, {"cleanup": metrics("", "noise words", [])}], "cleanup")
    assert result["scored"] == 1 and result["unavailable"] == 1
    assert result["false_speech"] == 1


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_evaluation_uses_temporary_archive_and_reports_candidate_failure(
    tmp_path, monkeypatch
):
    from omi_local.settings import Settings
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\x01\0" * 16000)
    (tmp_path / "fixture.wav").write_bytes(output.getvalue())
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps([{
        "id": "quiet", "audio": "fixture.wav", "category": "quiet", "language": "en",
        "reference": "Friday", "required_phrases": ["Friday"],
    }]))
    archive_paths = []

    def fake_settings(**kwargs):
        archive_paths.append(kwargs["data_dir"])
        assert kwargs["_env_file"] is None
        return Settings(**kwargs, stt_filter_speech=False)

    monkeypatch.setattr("scripts.evaluate_audio.Settings", fake_settings)

    async def unavailable(data):
        raise FileNotFoundError

    monkeypatch.setattr("omi_local.transcription.enhance_audio", unavailable)
    client_type = httpx.AsyncClient

    def mock_client(**kwargs):
        return client_type(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"text": "Friday"})
        ), **kwargs)

    monkeypatch.setattr("scripts.evaluate_audio.httpx.AsyncClient", mock_client)
    result = await evaluate(manifest)
    assert result["groups"]["category:quiet"]["processed"]["wer"] == 0
    assert result["groups"]["overall"]["cleanup"]["unavailable"] == 1
    assert archive_paths and all(not path.exists() for path in archive_paths)
    assert not list(tmp_path.glob("*.sqlite3"))
