"""Bounded, cancellable downloads of one account's complete private profile archives."""

import asyncio
import json
import queue
import threading
import zipfile
from collections.abc import Callable
from typing import TYPE_CHECKING, BinaryIO
from uuid import UUID

from anyio import CancelScope
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .archive_stream import write_archive

if TYPE_CHECKING:
    from .profiles import Profiles


CHUNK_BYTES = 64 * 1024
BUFFER_CHUNKS = 4


class ExportStopped(Exception):
    """The downstream disconnected; stop ZIP generation without further reads."""


class QueueSink:
    def __init__(self, output: queue.Queue, stopped: threading.Event, notify: Callable[[], None]):
        self.output, self.stopped, self.notify = output, stopped, notify
        self.position = 0

    def put(self, item) -> None:
        while not self.stopped.is_set():
            try:
                self.output.put(item, timeout=0.05)
                self.notify()
                return
            except queue.Full:
                continue
        raise ExportStopped

    def write(self, data: bytes) -> int:
        for offset in range(0, len(data), CHUNK_BYTES):
            self.put(data[offset : offset + CHUNK_BYTES])
        self.position += len(data)
        return len(data)

    def tell(self) -> int:
        return self.position

    def flush(self) -> None:
        if self.stopped.is_set():
            raise ExportStopped


def export_profiles(profiles: Profiles, sink: BinaryIO) -> None:
    """Single archives keep the existing format; ZIP64 bundles stream inner ZIPs."""
    directory = profiles.list()
    if [profile["id"] for profile in directory] == ["default"]:
        write_archive(profiles.runtimes["default"].memory.store, sink)
        return
    manifest = {"format": "daythread-profiles", "version": 1, "profiles": []}
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as bundle:
        for profile in directory:
            identity = profile["id"]
            if identity != "default" and str(UUID(identity)) != identity:
                raise ValueError("Invalid profile identity")
            filename = f"profiles/{identity}.zip"
            with bundle.open(filename, "w", force_zip64=True) as member:
                write_archive(profiles.runtimes[identity].memory.store, member)
            manifest["profiles"].append(
                {
                    "id": identity,
                    "name": profile["name"],
                    "archive": filename,
                }
            )
        bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))


class ArchiveDownload(StreamingResponse):
    """Backpressure bounds memory; cancellation joins the producer before release."""

    def __init__(self, producer: Callable[[BinaryIO], None], lock: threading.Lock, filename: str):
        self.admission, self.cleaned = lock, False
        self.stopped = threading.Event()
        self.output = queue.Queue(maxsize=BUFFER_CHUNKS)
        self.ready = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sink = QueueSink(self.output, self.stopped, self.notify)

        def produce():
            try:
                producer(self.sink)
            except ExportStopped:
                return
            except Exception:
                # Paths, object keys and credentials never enter the HTTP error.
                try:
                    self.sink.put(RuntimeError("Your archive could not be exported."))
                except ExportStopped:
                    return
            finally:
                try:
                    self.sink.put(None)
                except ExportStopped:
                    pass

        self.worker = threading.Thread(target=produce, name="archive-export", daemon=True)
        super().__init__(
            self.content(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
            background=BackgroundTask(self.cleanup),
        )
        self.worker.start()

    def notify(self) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.ready.set)

    async def content(self):
        self.loop = asyncio.get_running_loop()
        try:
            while True:
                self.ready.clear()
                try:
                    item = self.output.get_nowait()
                except queue.Empty:
                    if self.stopped.is_set():
                        break
                    await self.ready.wait()
                    continue
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            await self.cleanup()

    async def cleanup(self) -> None:
        with CancelScope(shield=True):
            self.stopped.set()
            self.ready.set()
            # Wait without consuming an executor slot: archive writes may already
            # occupy executor workers while waiting for this snapshot's SQL guard.
            while self.worker.is_alive():
                await asyncio.sleep(0.01)
            if not self.cleaned:
                self.cleaned = True
                self.admission.release()

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.cleanup()


def install_archive_export(app: FastAPI, profiles: Callable[[], Profiles]) -> None:
    admission = threading.Lock()

    @app.get("/v1/archive/export")
    def download():
        if not admission.acquire(blocking=False):
            raise HTTPException(
                409, "An archive export is already in progress. Please retry shortly."
            )
        try:
            owner_profiles = profiles()
            filename = (
                "omi-backup.zip" if len(owner_profiles.list()) == 1 else "daythread-profiles.zip"
            )
            return ArchiveDownload(
                lambda sink: export_profiles(owner_profiles, sink), admission, filename
            )
        except BaseException:
            admission.release()
            raise
