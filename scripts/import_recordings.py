#!/usr/bin/env python3
# Daythread standalone folder importer. Python 3.10+ and FFmpeg; no pip packages.
# ruff: noqa: UP017
# datetime.UTC is unavailable on Python 3.10 laptops.
"""Upload finished audio to a Daythread server without installing Daythread locally.

  brew install python ffmpeg
  python3 import_recordings.py --server https://daythread.example --parrot --watch
  python3 import_recordings.py --server http://home-core:8787 --folder ~/Recordings --once

Watching imports new/changed files; --include-existing also imports older files.
--once processes current files and exits. --dry-run previews without uploading.
Parrot mode mixes matching system/microphone CAF tracks, including on-device-only
recordings: a folder cannot reveal Parrot's privacy flags. Originals are never changed.
Other folders use file modification time as the recording end unless --mtime-is-start.
Use DAYTHREAD_IMPORT_TOKEN for an optional server/proxy Bearer token.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

RATE, PART_SAMPLES, MAX_SAMPLES = 16000, 16000 * 120, 16000 * 43200
PARROT = re.compile(r"^(system|mic)_(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\dZ)\.caf$")
EXTENSIONS = {".wav", ".wave", ".m4a", ".mp3", ".mp4", ".ogg", ".oga", ".opus",
              ".flac", ".aac", ".aif", ".aiff", ".caf", ".webm"}
LOG = logging.getLogger("daythread-import")


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class Recording:
    key: str
    paths: tuple[Path, ...]
    started_at: datetime | None
    source_id: str
    vendor_id: str

    def signature(self) -> str:
        stats = [(str(p), s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
                 for p in self.paths for s in [p.stat()]]
        return hashlib.sha256(json.dumps(stats).encode()).hexdigest()


def discover(folder: Path, parrot: bool, recursive: bool = False) -> list[Recording]:
    folder = folder.resolve()
    if not folder.exists():
        return []
    pairs: dict[str, dict[str, Path]] = {}
    result = []
    for path in sorted(folder.rglob("*") if recursive else folder.iterdir()):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(folder):
            continue
        if parrot:
            match = PARROT.fullmatch(path.name)
            if match:
                pairs.setdefault(str(path.parent / match[2]), {})[match[1]] = path
        elif path.suffix.lower() in EXTENSIONS:
            result.append(Recording(str(path), (path,), None, path.name, "manual"))
    for key, tracks in sorted(pairs.items()):
        suffix = Path(key).name
        try:
            start = datetime.strptime(suffix, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        paths = tuple(tracks[role] for role in ("system", "mic") if role in tracks)
        result.append(Recording(key, paths, start, f"parrot_{suffix}.wav", "parrot"))
    return result


def open_for_writing(paths: tuple[Path, ...]) -> bool:
    """lsof access flags distinguish capture writers from read-only playback."""
    executable = shutil.which("lsof") or "/usr/sbin/lsof"
    try:
        result = subprocess.run([executable, "-F", "a", "--", *map(str, paths)],
                                capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Cannot check open files; install lsof before importing") from exc
    if result.returncode not in (0, 1) or (result.returncode == 1 and result.stderr.strip()):
        raise ValueError("lsof could not check recording writers; retrying")
    return any(line in (b"aw", b"au") for line in result.stdout.splitlines())


class State:
    """Append-only baseline/acknowledgement ledger; one process per destination."""

    def __init__(self, path: Path | None, *, lock: bool = True):
        self.lock = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink() or path.with_suffix(".lock").is_symlink():
                raise ValueError("State and lock files must not be symbolic links")
            if lock:
                self.lock = path.with_suffix(".lock").open("a")
                try:
                    fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self.lock.close()
                    raise ValueError("An importer already owns this destination/index") from None
        self.db = sqlite3.connect(str(path) if path is not None else ":memory:")
        self.db.executescript("CREATE TABLE IF NOT EXISTS initialized (id INTEGER PRIMARY KEY);"
                              "CREATE TABLE IF NOT EXISTS receipts (key TEXT, signature TEXT,"
                              "status TEXT, PRIMARY KEY(key,signature,status));"
                              "CREATE TABLE IF NOT EXISTS digests (digest TEXT PRIMARY KEY);"
                              "CREATE TABLE IF NOT EXISTS scope (value TEXT PRIMARY KEY);")
        if path is not None:
            path.chmod(0o600)

    @property
    def initialized(self) -> bool:
        return bool(self.db.execute("SELECT 1 FROM initialized").fetchone())

    def bind(self, scope: str) -> None:
        with self.db:
            old = self.db.execute("SELECT value FROM scope").fetchone()
            if old and old[0] != scope:
                raise ValueError("Index belongs to another destination/folder; use another --state")
            self.db.execute("INSERT OR IGNORE INTO scope VALUES(?)", (scope,))

    def initialize(self, recordings: list[Recording]) -> None:
        with self.db:
            for recording in recordings:
                try:
                    self.db.execute("INSERT OR IGNORE INTO receipts VALUES(?,?,'baseline')",
                                    (recording.key, recording.signature()))
                except FileNotFoundError:
                    continue
            self.db.execute("INSERT OR IGNORE INTO initialized VALUES(1)")

    def contains(self, recording: Recording, signature: str, status: str = "done") -> bool:
        return bool(self.db.execute(
            "SELECT 1 FROM receipts WHERE key=? AND signature=? AND status=?",
            (recording.key, signature, status),
        ).fetchone())

    def record(self, recording: Recording, signature: str, status: str = "done") -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO receipts VALUES(?,?,?)",
                            (recording.key, signature, status))

    def has_digest(self, digest: str) -> bool:
        return bool(self.db.execute("SELECT 1 FROM digests WHERE digest=?", (digest,)).fetchone())

    def record_digest(self, digest: str) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO digests VALUES(?)", (digest,))

    def close(self) -> None:
        self.db.close()
        if self.lock is not None:
            self.lock.close()


def convert(recording: Recording, output: Path) -> int:
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-n"]
    for path in recording.paths:
        command.extend(["-protocol_whitelist", "file,pipe", "-i", str(path)])
    if len(recording.paths) == 2:
        command.extend(["-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:"
                        "dropout_transition=0:normalize=1"])
    else:
        command.extend(["-map", "0:a:0"])
    command.extend(["-vn", "-ac", "1", "-ar", str(RATE), "-t", "43200.001",
                    "-f", "s16le", "-c:a", "pcm_s16le", str(output)])
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(
            "FFmpeg could not decode/mix the recording; originals were preserved"
        ) from exc
    samples = output.stat().st_size // 2
    if output.stat().st_size % 2 or not 0 < samples <= MAX_SAMPLES:
        raise ValueError("Recording must contain audio and be at most twelve hours long")
    return samples


def parts(pcm: Path) -> Iterator[bytes]:
    with pcm.open("rb") as stream:
        while data := stream.read(PART_SAMPLES * 2):
            output = io.BytesIO()
            with wave.open(output, "wb") as writer:
                writer.setparams((1, 2, RATE, 0, "NONE", "PCM"))
                writer.writeframes(data)
            yield output.getvalue()


def file_digest(recording: Recording) -> str:
    digest = hashlib.sha256()
    for path in recording.paths:
        current = hashlib.sha256() if recording.vendor_id == "parrot" else digest
        if recording.vendor_id == "parrot":
            digest.update(path.name.split("_", 1)[0].encode() + b"\0")
        with path.open("rb") as stream:
            while data := stream.read(1024 * 1024):
                current.update(data)
        if recording.vendor_id == "parrot":
            digest.update(current.digest())
    return digest.hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Uploader:
    def __init__(self, server: str, profile: str = "default", token: str = "",
                 mtime_is_start: bool = False):
        parsed = urllib.parse.urlsplit(server)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Use a server HTTP(S) URL without credentials, query or fragment")
        if profile != "default":
            profile = str(UUID(profile))
        if any(ord(char) < 32 or ord(char) == 127 for char in token):
            raise ValueError("The server token must not contain control characters")
        self.server, self.profile, self.token = server.rstrip("/"), profile, token
        self.mtime_is_start = mtime_is_start
        self.state: State | None = None
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method: str, path: str, body: dict | None = None,
                content: bytes | None = None, params: dict | None = None) -> dict:
        url = self.server + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"X-Omi-Profile": self.profile, "User-Agent": "Daythread-Folder-Importer/1",
                   "Content-Type": "audio/wav" if content is not None else "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = content if content is not None else json.dumps(body).encode()
        try:
            with self.opener.open(urllib.request.Request(url, data=data, headers=headers,
                                                        method=method), timeout=120) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError("Server response exceeded the size limit")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("Invalid server response")
            return result
        except urllib.error.HTTPError as exc:
            raise ValueError(
                f"Server returned HTTP {exc.code}; check server, profile and access"
            ) from None
        except urllib.error.URLError:
            raise ValueError("Server unavailable; check its address, connection and TLS") from None

    def upload(self, recording: Recording, signature: str) -> str:
        LOG.info("Preparing %s (%s track(s))", recording.source_id, len(recording.paths))
        digest = file_digest(recording)
        if recording.signature() != signature or open_for_writing(recording.paths):
            raise ValueError("Recording changed or reopened; waiting for it to finish")
        if self.state and self.state.has_digest(digest):
            return "duplicate"
        duplicate = self.request("POST", "/v1/recording-uploads/check", {"digest": digest})
        if duplicate.get("duplicate") is True:
            if self.state:
                self.state.record_digest(digest)
            return "duplicate"
        with tempfile.TemporaryDirectory(prefix="daythread-import-") as scratch:
            pcm = Path(scratch) / "recording.pcm"
            samples = convert(recording, pcm)
            if recording.signature() != signature or open_for_writing(recording.paths):
                raise ValueError("Recording changed or reopened during conversion; retrying")
            duration = timedelta(seconds=samples / RATE)
            modified = datetime.fromtimestamp(max(p.stat().st_mtime for p in recording.paths),
                                              tz=timezone.utc)
            start = recording.started_at or (
                modified if self.mtime_is_start else modified - duration
            )
            end = start + duration
            plan = self.request("POST", "/v1/recording-uploads/prepare", {
                "vendor_id": recording.vendor_id, "file_digest": digest,
                "digests": [hashlib.sha256(part).hexdigest() for part in parts(pcm)],
                "sample_count": samples, "source_id": recording.source_id,
                "started_at": stamp(start), "ended_at": stamp(end),
                "treat_as_meeting": recording.vendor_id == "parrot",
            })
            if plan.get("status") == "duplicate":
                if self.state:
                    self.state.record_digest(digest)
                return "duplicate"
            expected = [hashlib.sha256(part).hexdigest() for part in parts(pcm)]
            if (plan.get("status") != "upload" or not isinstance(plan.get("parts"), list)
                    or [part.get("digest") for part in plan["parts"]] != expected):
                raise ValueError("Server returned an invalid upload plan")
            start = datetime.fromisoformat(plan["started_at"])
            for index, (part, entry) in enumerate(zip(parts(pcm), plan["parts"])):
                if entry.get("uploaded") is True:
                    continue
                at = start + timedelta(seconds=index * 120)
                params = {"device_id": "manual-web", "source_kind": "manual_upload",
                          "source_id": plan["source_id"],
                          "vendor_id": plan.get("vendor_id", "manual"),
                          "started_at": stamp(at), "recording_id": plan["recording_id"],
                          "part_index": index, "part_count": len(plan["parts"]),
                          "recording_started_at": plan["started_at"],
                          "recording_ended_at": plan["ended_at"]}
                if plan.get("conversation_source_id"):
                    params["conversation_source_id"] = plan["conversation_source_id"]
                LOG.info("Uploading %s, part %s/%s", recording.source_id, index + 1, len(expected))
                receipt = self.request(
                    "PUT", f"/v1/audio/{entry['id']}", content=part, params=params,
                )
                part_end = at + timedelta(seconds=(len(part) - 44) / (2 * RATE))
                if (receipt.get("id") != entry["id"] or receipt.get("device_id") != "manual-web"
                        or datetime.fromisoformat(receipt["started_at"]) != at
                        or datetime.fromisoformat(receipt["ended_at"]) != part_end):
                    raise ValueError("Server acknowledgement did not match the uploaded audio")
        if self.state:
            self.state.record_digest(digest)
        return "imported"


class FolderWatcher:
    def __init__(self, folder: Path, parrot: bool, state: State,
                 importer: Callable[[Recording, str], str], *,
                 include_existing: bool = False, settle_seconds: float = 30,
                 recursive: bool = False, dry_run: bool = False):
        self.folder, self.parrot, self.state, self.importer = folder, parrot, state, importer
        self.include_existing, self.settle = include_existing, settle_seconds
        self.recursive, self.dry_run = recursive, dry_run
        self.observed: dict[str, tuple[str, float]] = {}
        self.retry_at: dict[str, float] = {}

    def scan(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        result = {"imported": 0, "pending": 0, "failed": 0}
        recordings = discover(self.folder, self.parrot, self.recursive)
        if not self.state.initialized:
            self.state.initialize([] if self.include_existing else recordings)
        for recording in recordings:
            try:
                signature = recording.signature()
                if (self.state.contains(recording, signature)
                        or (not self.include_existing
                            and self.state.contains(recording, signature, "baseline"))):
                    continue
                prior = self.observed.get(recording.key)
                if not prior or prior[0] != signature:
                    self.observed[recording.key] = (signature, now)
                    self.retry_at.pop(recording.key, None)
                    result["pending"] += 1
                    continue
                if (now - prior[1] < self.settle or now < self.retry_at.get(recording.key, 0)
                        or open_for_writing(recording.paths)):
                    result["pending"] += 1
                    continue
                if self.dry_run:
                    LOG.info("Would import %s: %s", recording.source_id,
                             ", ".join(str(p) for p in recording.paths))
                else:
                    disposition = self.importer(recording, signature)
                    LOG.info("%s: %s", disposition.capitalize(), recording.source_id)
                self.state.record(recording, signature)
                result["imported"] += 1
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                LOG.error("%s: %s", recording.source_id, exc)
                self.retry_at[recording.key] = now + 60
                result["failed"] += 1
        return result


def install(target: Path) -> None:
    target = target.expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or (target.exists() and b"# Daythread standalone folder importer."
                               not in target.read_bytes()[:200]):
        raise ValueError("Installation target exists and is not this importer")
    source = Path(__file__).read_bytes()
    target.write_bytes(source)
    target.chmod(0o755)
    print(f"Installed {target}\nRun: {target} --server YOUR_DAYTHREAD_URL --parrot")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--server", default=os.environ.get("DAYTHREAD_SERVER_URL"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--parrot", action="store_true", help="watch Parrot's local CAF recordings")
    mode.add_argument("--folder", type=Path, help="watch another audio folder")
    parser.add_argument("--profile", default="default", help="destination profile UUID or default")
    parser.add_argument("--include-existing", action="store_true",
                        help="also import existing recordings")
    run_mode = parser.add_mutually_exclusive_group()
    run_mode.add_argument("--watch", action="store_true",
                          help="keep watching for recordings (default)")
    run_mode.add_argument("--once", action="store_true",
                          help="import current closed files, then exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="preview files; no uploads or saved receipts")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--mtime-is-start", action="store_true",
                        help="generic folder: mtime is capture start")
    parser.add_argument("--settle-seconds", type=float, default=30,
                        help="unchanged period (default: 30)")
    parser.add_argument("--poll-seconds", type=float, default=5, help="poll interval (default: 5)")
    parser.add_argument("--state", type=Path, help="override the local acknowledgement database")
    parser.add_argument("--install", nargs="?",
                        const=str(Path.home() / ".local/bin/daythread-import"), type=Path,
                        help="install this one file as a command; optional destination path")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    os.umask(0o077)
    if args.install:
        install(args.install)
        return 0
    if not args.server and not args.dry_run:
        parser.error("supply --server or DAYTHREAD_SERVER_URL (the remote Daythread server)")
    if (not math.isfinite(args.settle_seconds) or not math.isfinite(args.poll_seconds)
            or args.settle_seconds < 0 or args.poll_seconds <= 0):
        parser.error("settle seconds must be nonnegative; poll seconds must be positive")
    if not args.dry_run and not shutil.which("ffmpeg"):
        parser.error("FFmpeg is required. On macOS: brew install ffmpeg")
    parrot = args.parrot or args.folder is None
    folder = args.folder or Path.home() / "Library/Application Support/Parrot/Audio"
    folder = folder.expanduser().resolve()
    uploader = Uploader(args.server or "http://localhost", args.profile,
                        os.environ.get("DAYTHREAD_IMPORT_TOKEN", ""), args.mtime_is_start)
    destination = json.dumps([uploader.server, uploader.profile])
    scope = json.dumps([destination, str(folder), parrot, args.mtime_is_start])
    indexes = Path.home() / "Library/Application Support/Daythread Folder Import"
    state_path = args.state or indexes / (hashlib.sha256(scope.encode()).hexdigest()[:24]
                                        + ".sqlite3")
    state = State(None if args.dry_run else state_path.expanduser())
    state.bind(scope)
    content_path = indexes / "acknowledged" / (hashlib.sha256(destination.encode()).hexdigest()
                                             + ".sqlite3")
    uploader.state = State(None if args.dry_run else content_path)
    uploader.state.bind(destination)
    watcher = FolderWatcher(folder, parrot, state, uploader.upload,
                            include_existing=args.include_existing or args.once,
                            settle_seconds=args.settle_seconds, recursive=args.recursive,
                            dry_run=args.dry_run)
    LOG.info("Watching %s; destination profile %s. Ctrl+C stops.", folder, args.profile)
    LOG.info("Parrot folder mode includes on-device-only audio; tracks are mixed when both exist."
             if parrot else "Using file modification time as recording %s.",
             *([] if parrot else ["start" if args.mtime_is_start else "end"]))
    try:
        watcher.scan()
        if args.once:
            time.sleep(args.settle_seconds)
            result = watcher.scan()
            LOG.info("Finished: %(imported)s imported/duplicate, "
                     "%(pending)s still recording/changing, "
                     "%(failed)s failed.", result)
            return 1 if result["failed"] or result["pending"] else 0
        while True:
            time.sleep(args.poll_seconds)
            watcher.scan()
    except KeyboardInterrupt:
        LOG.info("Stopped; completed uploads are remembered for the next run.")
        return 0
    finally:
        state.close()
        uploader.state.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, sqlite3.Error) as error:
        LOG.error("%s", error)
        sys.exit(1)
