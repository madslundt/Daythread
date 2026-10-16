"""Quality metrics and live opt-in behavior using local fixtures and mock STT."""

import io
import json
import wave

import httpx
import pytest

from scripts.evaluate_audio import aggregate, evaluate, metrics, words


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
        return Settings(**kwargs)

    monkeypatch.setattr("scripts.evaluate_audio.Settings", fake_settings)

    async def unavailable(data):
        raise FileNotFoundError

    monkeypatch.setattr("omi_local.inference.enhance_audio", unavailable)
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
