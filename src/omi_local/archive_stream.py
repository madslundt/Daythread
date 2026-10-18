"""Portable ZIP output with a metadata snapshot and one bounded recording in memory."""

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from .decision_export import diary_markdown, export_days
from .weekly_models import markdown as weekly_markdown

if TYPE_CHECKING:
    from .store import Store


MAX_RECORDING_BYTES = 32_000_000


def write_archive(store: Store, sink: BinaryIO) -> None:
    """Write the existing restore format without staging audio or a complete ZIP.

    The archive write guard pins both metadata and recording objects until the
    producer finishes. This also excludes managed explicit deletion of S3 objects;
    immutable hashes detect unavailable or externally changed recordings. A slow
    download temporarily delays writes to this private profile, never other people.
    Only the portable SQLite metadata snapshot requires working disk space.
    """
    exports = store.root / ".exports"
    exports.mkdir(mode=0o700, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="omi-stream-", dir=exports))
    try:
        snapshot = work / "omi.sqlite3"
        with store.connect() as guard:
            guard.execute("BEGIN IMMEDIATE")
            with store.connect() as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
                target.execute("DELETE FROM notification_deliveries")
                target.execute("DELETE FROM notification_devices")
                target.execute("DELETE FROM audio_deletions")
                target.commit()
                target.execute("VACUUM")
            with (
                closing(sqlite3.connect(snapshot)) as db,
                zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive,
            ):
                db.row_factory = sqlite3.Row
                archive.write(snapshot, "omi.sqlite3")
                for row in db.execute("SELECT id,digest,audio_available FROM audio"):
                    if not row["audio_available"]:
                        continue
                    data = store.read_audio(row["id"])
                    if len(data) > MAX_RECORDING_BYTES:
                        raise ValueError("Stored recording exceeds the export audio budget")
                    if hashlib.sha256(data).hexdigest() != row["digest"]:
                        raise ValueError("Stored recording checksum mismatch")
                    archive.writestr(f"audio/{row['id']}.wav", data)
                    del data
                for day, content in export_days(db):
                    archive.writestr(f"diary/{day}.md", diary_markdown(db, day, content))
                for row in db.execute("SELECT id,content FROM weekly_versions"):
                    archive.writestr(
                        f"weekly/{row['id']}.md",
                        weekly_markdown(json.loads(row["content"]), stale=True),
                    )
    finally:
        shutil.rmtree(work, ignore_errors=True)
