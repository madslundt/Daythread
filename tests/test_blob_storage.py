import asyncio
import hashlib
import io
import os
import shutil
import wave
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.blob_storage import S3AudioStorage
from omi_local.recording_uploads import available
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.worker import process_one

ClientError = pytest.importorskip("botocore.exceptions").ClientError


class Objects:
    def __init__(self):
        self.objects = {}
        self.failure = None

    def _check(self):
        if self.failure:
            raise ClientError({"Error": {"Code": self.failure},
                               "ResponseMetadata": {"HTTPStatusCode": 403}}, "Storage")

    def put_object(self, **request):
        self._check()
        key = request["Bucket"], request["Key"]
        if key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"},
                               "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
        assert request["ServerSideEncryption"] == "AES256"
        assert request["IfNoneMatch"] == "*"
        self.objects[key] = request
        return {}

    def get_object(self, **request):
        self._check()
        try:
            saved = self.objects[request["Bucket"], request["Key"]]
        except KeyError:
            raise ClientError({"Error": {"Code": "NoSuchKey"},
                               "ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject") from None
        return {"Body": io.BytesIO(saved["Body"]), "ContentLength": len(saved["Body"]),
                "Metadata": saved["Metadata"]}

    def head_object(self, **request):
        response = self.get_object(**request)
        response["Body"].close()
        return {}

    def delete_object(self, **request):
        self._check()
        self.objects.pop((request["Bucket"], request["Key"]), None)


def audio():
    result = io.BytesIO()
    with wave.open(result, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\x10\x10" * 16000)
    return result.getvalue()


def test_account_objects_are_private_immutable_and_checksum_verified(tmp_path):
    client, key, data = Objects(), str(uuid4()), audio()
    first = S3AudioStorage("private-recordings", "accounts/first", client=client)
    second = S3AudioStorage("private-recordings", "accounts/second", client=client)
    first.write(key, data)
    first.write(key, data)  # Lost acknowledgement retry preserves the existing object.
    assert first.read(key) == data
    assert not second.exists(key)
    assert len(client.objects) == 1
    with pytest.raises(ValueError, match="different content"):
        first.write(key, data + b"changed")
    client.objects["private-recordings", first._key(key)]["Body"] = b"corrupted"
    with pytest.raises(ValueError, match="checksum"):
        first.read(key)
    with pytest.raises(ValueError):
        first.write("../other-account", data)
    client.failure = "AccessDenied"
    with pytest.raises(ClientError):
        first.exists(key)


def test_s3_failure_never_acknowledges_an_unstored_recording(tmp_path):
    client = Objects()
    storage = S3AudioStorage("private-recordings", "accounts/example", client=client)
    store = Store(tmp_path, storage=storage)
    client.failure = "AccessDenied"
    key = uuid4()
    with pytest.raises(ClientError):
        store.ingest(key, "device", datetime.now(UTC), audio(), 1)
    assert store.get_audio(str(key)) is None
    assert not list(store.audio_dir.glob("*.wav"))


def test_s3_recording_playback_processing_export_and_deletion(tmp_path, monkeypatch):
    import boto3

    objects = Objects()
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: objects)
    settings = Settings(_env_file=None, data_dir=tmp_path, worker_enabled=False,
                        storage_bucket="private-recordings", storage_prefix="accounts/example")
    with TestClient(create_app(settings)) as client:
        memory = client.app.state.memory
        key, data = uuid4(), audio()
        memory.store.ingest(key, "device", datetime.now(UTC), data, 1)
        assert client.get(f"/v1/audio/{key}").content == data
        assert client.get("/v1/storage").json()["used_bytes"] == len(data)
        assert not list(memory.store.audio_dir.glob("*.wav"))

        class Inference:
            async def transcribe(self, data):
                return "A stored recording."

        memory.inference = Inference()
        assert asyncio.run(process_one(memory))
        assert memory.store.get_audio(str(key))["text"] == "A stored recording."
        assert client.get("/v1/transcripts").json()["items"][0]["audio_availability"] == "available"
        with memory.store.connect() as db:
            assert available(db, {"parts": [{"id": str(key),
                                            "digest": hashlib.sha256(data).hexdigest()}]}) == [True]
        output = memory.store.export_archive()
        try:
            with zipfile.ZipFile(output) as archive:
                assert archive.read(f"audio/{key}.wav") == data
                assert "omi.sqlite3" in archive.namelist()
        finally:
            shutil.rmtree(output.parent)
        assert client.delete(f"/v1/audio/{key}").status_code == 200
        assert client.get(f"/v1/audio/{key}").status_code == 404
        assert client.get("/v1/storage").json()["used_bytes"] == 0
        assert not objects.objects


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL not configured")
def test_postgres_and_s3_survive_loss_of_the_entire_working_directory(tmp_path):
    objects, key, data = Objects(), uuid4(), audio()
    schema = "test_blob_" + uuid4().hex
    storage = S3AudioStorage("private-recordings", "accounts/example", client=objects)
    first = Store(tmp_path / "old-host", database_url=os.environ["TEST_POSTGRES_URL"],
                  database_schema=schema, storage=storage)
    try:
        first.ingest(key, "device", datetime.now(UTC), data, 1)
        first.complete(str(key), "Customer data survived host replacement.")
        shutil.rmtree(first.root)
        second = Store(tmp_path / "new-host", database_url=os.environ["TEST_POSTGRES_URL"],
                       database_schema=schema, storage=storage)
        assert second.get_audio(str(key))["text"] == "Customer data survived host replacement."
        assert second.read_audio(str(key)) == data
        assert second.storage_usage() == len(data)
        assert not (second.root / "omi.sqlite3").exists()
        output = second.export_archive()
        try:
            with zipfile.ZipFile(output) as archive:
                assert archive.read(f"audio/{key}.wav") == data
        finally:
            shutil.rmtree(output.parent)
    finally:
        with first.database.pool.connection() as db:
            db.execute(f'DROP SCHEMA "{schema}" CASCADE')
