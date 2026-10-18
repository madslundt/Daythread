"""Durable recording objects, with a local backend for self-hosted archives."""

import base64
import hashlib
import os
import re
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Protocol
from uuid import UUID


class AudioStorage(Protocol):
    is_remote: bool

    def location(self, audio_id: str) -> str: ...
    def write(self, audio_id: str, data: bytes) -> None: ...
    def read(self, audio_id: str) -> bytes: ...
    def exists(self, audio_id: str) -> bool: ...
    def delete(self, audio_id: str) -> None: ...


def identifier(audio_id: str) -> str:
    return str(UUID(audio_id))


def audio_is_present(row) -> bool:
    """Use committed object metadata for remote audio, local existence otherwise.

    S3 objects are durable before their metadata row is accepted. Listing a day
    must not issue a separate network request for every recording. Read failures
    mark missing objects through the worker's existing missing_audio status.
    """
    value = dict(row)
    if not value.get("audio_available", True):
        return False
    if value["path"].startswith("s3://"):
        return value.get("error_code") != "missing_audio"
    return Path(value["path"]).is_file()


class LocalAudioStorage:
    is_remote = False

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def location(self, audio_id: str) -> str:
        return str(self.root / f"{identifier(audio_id)}.wav")

    def write(self, audio_id: str, data: bytes) -> None:
        path = Path(self.location(audio_id))
        descriptor, temporary = tempfile.mkstemp(prefix=".audio-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def read(self, audio_id: str) -> bytes:
        return Path(self.location(audio_id)).read_bytes()

    def exists(self, audio_id: str) -> bool:
        return Path(self.location(audio_id)).is_file()

    def delete(self, audio_id: str) -> None:
        Path(self.location(audio_id)).unlink(missing_ok=True)


class S3AudioStorage:
    is_remote = True

    def __init__(self, bucket: str, prefix: str, region: str = "", *, client=None):
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise ValueError("Configure a valid private S3 bucket")
        if not prefix or any(part in ("", ".", "..") for part in prefix.rstrip("/").split("/")):
            raise ValueError("Configure an account-scoped S3 prefix")
        if not re.fullmatch(r"[A-Za-z0-9/_-]+", prefix):
            raise ValueError("Invalid recording storage prefix")
        self.bucket, self.prefix = bucket, prefix.rstrip("/") + "/audio/"
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3", region_name=region or None,
                config=Config(connect_timeout=5, read_timeout=30,
                              retries={"mode": "standard", "max_attempts": 3}),
            )
        self.client = client

    def _key(self, audio_id: str) -> str:
        return self.prefix + identifier(audio_id) + ".wav"

    def location(self, audio_id: str) -> str:
        return f"s3://{self.bucket}/{self._key(audio_id)}"

    def write(self, audio_id: str, data: bytes) -> None:
        from botocore.exceptions import ClientError

        checksum = hashlib.sha256(data).digest()
        try:
            self.client.put_object(
                Bucket=self.bucket, Key=self._key(audio_id), Body=data,
                ContentType="audio/wav", ServerSideEncryption="AES256",
                ChecksumSHA256=base64.b64encode(checksum).decode(),
                Metadata={"sha256": checksum.hex()}, IfNoneMatch="*",
            )
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
            # A retry after a lost acknowledgement may find an already durable object.
            # Never replace different content under an existing recording ID.
            if self.read(audio_id) != data:
                raise ValueError("Recording object already exists with different content") from None

    def read(self, audio_id: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(audio_id))
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "NotFound", "404"):
                raise FileNotFoundError("Recording object is unavailable") from None
            raise
        with closing(response["Body"]) as body:
            if response["ContentLength"] > 32_000_000:
                raise ValueError("Stored recording exceeds the audio budget")
            data = body.read(32_000_001)
        if len(data) != response["ContentLength"]:
            raise ValueError("Stored recording is incomplete")
        expected = response.get("Metadata", {}).get("sha256")
        if not expected or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Stored recording checksum mismatch")
        return data

    def exists(self, audio_id: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(audio_id))
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "NotFound", "404"):
                return False
            raise

    def delete(self, audio_id: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(audio_id))


def storage_from_settings(settings, audio_dir: Path, profile_id: str | None = None) -> AudioStorage:
    if not settings.storage_bucket:
        return LocalAudioStorage(audio_dir)
    prefix = settings.storage_prefix.rstrip("/")
    if profile_id:
        prefix += "/profiles/" + identifier(profile_id)
    return S3AudioStorage(settings.storage_bucket, prefix, settings.storage_region)
