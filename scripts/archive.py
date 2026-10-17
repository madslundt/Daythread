"""Back up local Omi data or restore it to a new directory, without exporting credentials."""

import argparse
import hashlib
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath
from uuid import UUID

from omi_local.decision_restore import validate_decisions
from omi_local.label_schema import validate_labels
from omi_local.settings import Settings
from omi_local.store import Store


def member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if "\\" in name or path.is_absolute() or ".." in path.parts:
        raise ValueError("Archive contains an unsafe path")
    if path.as_posix() == "omi.sqlite3":
        return path
    if len(path.parts) != 2:
        raise ValueError("Archive contains an unsupported file")
    folder, filename = path.parts
    if folder == "audio" and filename.endswith(".wav"):
        if str(UUID(filename[:-4])) != filename[:-4]:
            raise ValueError("Invalid recording ID in archive")
    elif folder == "weekly" and filename.endswith(".md"):
        if str(UUID(filename[:-3])) != filename[:-3]:
            raise ValueError("Invalid weekly review version ID")
    elif folder == "diary" and filename.endswith(".md"):
        if date.fromisoformat(filename[:-3]).isoformat() != filename[:-3]:
            raise ValueError("Invalid diary date in archive")
    else:
        raise ValueError("Archive contains an unsupported file")
    return path


def backup(root: Path, output: Path, recipient: str | None = None) -> None:
    if not (root / "omi.sqlite3").is_file():
        raise ValueError("No Omi database found in the selected data directory")
    if output.exists():
        raise ValueError("Choose a new backup filename; existing backups are preserved")
    source = Store(root).export_archive()
    try:
        with source.open("rb") as reader, output.open("xb") as writer:
            try:
                if recipient:
                    subprocess.run(
                        ["age", "--recipient", recipient],
                        stdin=reader,
                        stdout=writer,
                        stderr=subprocess.PIPE,
                        check=True,
                    )
                else:
                    shutil.copyfileobj(reader, writer)
            except BaseException:
                output.unlink(missing_ok=True)
                raise
        output.chmod(0o600)
    finally:
        shutil.rmtree(source.parent)


def validate_database(root: Path) -> None:
    with sqlite3.connect(root / "omi.sqlite3") as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Archive database failed its integrity check")
        validate_decisions(db)
        validate_labels(db)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(audio)")}
        for row in db.execute("SELECT * FROM audio"):
            key = str(UUID(row["id"]))
            if key != row["id"]:
                raise ValueError("Archive has an invalid recording ID")
            available = row["audio_available"] if "audio_available" in columns else True
            if available:
                path = root / "audio" / f"{key}.wav"
                if not path.is_file():
                    raise ValueError(f"Archive is missing recording {key}")
                with path.open("rb") as audio:
                    digest = hashlib.file_digest(audio, "sha256").hexdigest()
                if digest != row["digest"]:
                    raise ValueError(f"Archive checksum mismatch for recording {key}")


def restore(source: Path, destination: Path, max_bytes: int, identity: Path | None = None) -> None:
    if identity:
        with tempfile.TemporaryDirectory(prefix="omi-decrypt-") as work:
            plaintext = Path(work) / "archive.zip"
            with plaintext.open("xb") as writer:
                subprocess.run(
                    ["age", "--decrypt", "--identity", str(identity), str(source)],
                    stdout=writer,
                    stderr=subprocess.PIPE,
                    check=True,
                )
            return restore(plaintext, destination, max_bytes)
    # Exclusive creation prevents accidentally replacing a live or existing archive.
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if "omi.sqlite3" not in names or len(names) != len(set(names)):
                raise ValueError("Archive must contain one database and no duplicate entries")
            if sum(member.file_size for member in members) > max_bytes:
                raise ValueError("Archive exceeds the configured extracted size limit")
            for member in members:
                relative = member_path(member.filename)
                mode = member.external_attr >> 16
                if member.is_dir() or stat.S_ISLNK(mode):
                    raise ValueError("Archive must contain ordinary files only")
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.open(member) as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                target.chmod(0o600)
        validate_database(destination)
        store = Store(destination)  # Migrate supported versions and rebase audio paths.
        with store.connect() as db:
            db.execute("UPDATE phone_observations SET historical=1")
            # Also protect restores of backups made before device bindings were
            # excluded from exports. Phones register again with the restored archive.
            db.execute("DELETE FROM notification_deliveries")
            db.execute("DELETE FROM notification_devices")
        with store.connect() as db:
            db.execute("VACUUM")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except BaseException:
        shutil.rmtree(destination)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("backup", help="Save SQLite, recordings and diaries to a ZIP")
    export.add_argument("output", type=Path)
    export.add_argument("--data-dir", type=Path)
    export.add_argument("--recipient", help="Encrypt to an age/SSH public recipient with age")
    load = commands.add_parser("restore", help="Restore into a new, non-existing directory")
    load.add_argument("archive", type=Path)
    load.add_argument("destination", type=Path)
    load.add_argument("--max-bytes", type=int, default=100_000_000_000)
    load.add_argument("--identity", type=Path, help="Decrypt with this age/SSH identity file")
    args = parser.parse_args()
    try:
        if args.command == "backup":
            backup(args.data_dir or Settings().data_dir, args.output, args.recipient)
            print(f"Saved archive: {args.output}")
        else:
            restore(args.archive, args.destination, args.max_bytes, args.identity)
            print(f"Restored archive: {args.destination}")
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print(f"Archive operation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except subprocess.CalledProcessError:
        print(
            "Archive encryption/decryption failed; check age recipient or identity.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
