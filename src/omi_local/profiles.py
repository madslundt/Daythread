"""Named local archives and their workers; settings are shared by every profile."""

import asyncio
import hashlib
import sqlite3
from contextlib import closing, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .embeddings import Embeddings, run_embedding_worker
from .generation_jobs import GenerationJobs
from .inference import Inference
from .notifications import NotificationService
from .omi_api import OmiAPI
from .pocket_api import PocketAPI
from .service import Memory
from .settings import Settings
from .store import Store
from .vendor_imports import TranscriptAdapter
from .weekly import WeeklyReviews
from .worker import run_worker

DEFAULT_PROFILE = "default"
selected_profile: ContextVar[str] = ContextVar("omi_profile", default=DEFAULT_PROFILE)


def profile_schema(account_schema: str, profile_id: str) -> str:
    """Stable schema names fit PostgreSQL's identifier limit for every account."""
    return "archive_" + hashlib.sha256(
        f"{account_schema}:{UUID(profile_id)}".encode()
    ).hexdigest()[:40]


class NewProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Use a nonempty profile name without control characters")
        return value


@dataclass
class ProfileRuntime:
    memory: Memory
    weekly: WeeklyReviews
    omi: OmiAPI
    weekly_worker: asyncio.Task
    worker: asyncio.Task | None
    embedding_worker: asyncio.Task | None
    jobs: GenerationJobs
    generation_worker: asyncio.Task
    notification_worker: asyncio.Task
    vendors: dict[str, TranscriptAdapter]


class Profiles:
    def __init__(
        self, root: Path, client: httpx.AsyncClient, *, settings: Settings | None = None,
    ):
        from .postgres import PostgresDatabase

        self.database = (
            PostgresDatabase(settings.database_url.get_secret_value(), settings.database_schema)
            if settings and settings.database_url else None
        )
        self.root = root
        self.client = client
        self.runtimes: dict[str, ProfileRuntime] = {}
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS profile_events (
                    sequence INTEGER PRIMARY KEY, profile_id TEXT NOT NULL,
                    action TEXT NOT NULL, name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT OR IGNORE INTO profiles VALUES ('default', 'Default', 'default');
            """)

    def connect(self):
        if self.database:
            return self.database.connect()
        return sqlite3.connect(self.root / "profiles.sqlite3", timeout=30)

    def list(self) -> list[dict]:
        db = self.connect()
        try:
            return [
                {"id": row[0], "name": row[1]}
                for row in db.execute("SELECT id,name FROM profiles ORDER BY rowid")
            ]
        finally:
            db.close()

    def create(self, body: NewProfile) -> dict:
        profile = {"id": str(uuid4()), "name": body.name}
        db = self.connect()
        try:
            with db:
                db.execute(
                    "INSERT INTO profiles VALUES (?,?,?)",
                    (profile["id"], body.name, body.name.casefold()),
                )
                db.execute(
                    "INSERT INTO profile_events(profile_id,action,name) VALUES (?,'created',?)",
                    (profile["id"], body.name),
                )
        except sqlite3.IntegrityError:
            raise ValueError("A profile with this name already exists") from None
        finally:
            db.close()
        return profile

    def start(
        self, profile_id: str, settings: Settings, store: Store | None = None
    ) -> ProfileRuntime:
        if profile_id in self.runtimes:
            return self.runtimes[profile_id]
        # Only catalogue IDs reach this method; names never become filesystem paths.
        if store is None:
            from .blob_storage import storage_from_settings

            directory = self.root / "profiles" / str(UUID(profile_id))
            schema = profile_schema(settings.database_schema, profile_id)
            store = Store(
                directory,
                database_url=(
                    settings.database_url.get_secret_value() if settings.database_url else None
                ),
                database_schema=schema,
                storage=storage_from_settings(settings, directory / "audio", profile_id),
            )
        if profile_id != DEFAULT_PROFILE:
            store.save_settings(self.runtimes[DEFAULT_PROFILE].memory.store.runtime_settings())
        memory = Memory(store, settings, Inference(settings, self.client))
        memory.notifications = NotificationService(store, settings, profile_id)
        jobs = GenerationJobs(memory)
        memory.retriever.embeddings = Embeddings(settings, self.client)
        weekly = WeeklyReviews(memory)
        worker = embedding_worker = None
        if settings.worker_enabled:
            store.recover()
            worker = asyncio.create_task(run_worker(memory))
            if memory.retriever.set_id:
                embedding_worker = asyncio.create_task(
                    run_embedding_worker(
                        store, memory.retriever.embeddings, memory.retriever.set_id
                    )
                )
        runtime = ProfileRuntime(
            memory,
            weekly,
            OmiAPI(settings, self.client, store),
            asyncio.create_task(weekly.run()),
            worker,
            embedding_worker,
            jobs,
            asyncio.create_task(jobs.run()),
            asyncio.create_task(memory.notifications.run()),
            {},
        )
        runtime.vendors = {"omi": runtime.omi, "pocket": PocketAPI(settings, self.client, store)}
        self.runtimes[profile_id] = runtime
        return runtime

    def rename(self, profile_id: str, body: NewProfile) -> dict:
        with closing(self.connect()) as db:
            try:
                with db:
                    updated = db.execute(
                        "UPDATE profiles SET name=?,name_key=? WHERE id=?",
                        (body.name, body.name.casefold(), profile_id),
                    )
                    if not updated.rowcount:
                        raise KeyError(profile_id)
                    db.execute(
                        "INSERT INTO profile_events(profile_id,action,name) VALUES (?,'renamed',?)",
                        (profile_id, body.name),
                    )
            except sqlite3.IntegrityError:
                raise ValueError("A profile with this name already exists") from None
        return {"id": profile_id, "name": body.name}

    async def close(self) -> None:
        tasks = [
            task
            for runtime in self.runtimes.values()
            for task in (runtime.worker, runtime.embedding_worker, runtime.weekly_worker,
                         runtime.generation_worker, runtime.notification_worker)
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        for runtime in self.runtimes.values():
            await runtime.memory.notifications.close()
