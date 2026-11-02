"""Durable chat handoff and exchange transactions, independent of a phone connection."""

import json
from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from pydantic import ConfigDict, field_validator

from .domain import ChatRequest, ChatScopeConflict, EvidenceChanged, stamp, utc_now

if TYPE_CHECKING:
    from .store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS deleted_chat_jobs (id TEXT PRIMARY KEY);
"""
SCOPE_FIELDS = ("day", "meeting_id", "start_day", "end_day")


class ChatParameters(ChatRequest):
    model_config = ConfigDict(extra="forbid")

    @field_validator("question")
    @classmethod
    def nonempty_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a question")
        return value


def user_message_id(job_id: str) -> str:
    return str(uuid5(UUID(job_id), "user"))


def ensure_chat_available(db: Any, session_id: str, request_id: str | None = None) -> None:
    # Memory is also used without the durable worker in standalone scripts/tests.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='generation_jobs'").fetchone():
        return
    pending = db.execute(
        "SELECT id FROM generation_jobs WHERE kind='chat' AND state IN ('queued','running') "
        "AND json_extract(parameters,'$.session_id')=? AND id!=?",
        (session_id, request_id or ""),
    ).fetchone()
    if pending:
        raise ChatScopeConflict("A reply is already being prepared in this chat")


def save_chat_exchange(store: Store, job_id: str, result: dict) -> dict:
    """One transaction owns the question, answer, provenance and completed job.

    A crash after this commit must never run inference or append the exchange again.
    Deleting a session/job while inference runs prevents this commit entirely.
    """
    session_id, sources = result["session_id"], result["sources"]
    ids = {"user_message_id": user_message_id(job_id), "message_id": job_id}
    completed = result | ids
    now = stamp(utc_now())
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute(
            "SELECT state,result FROM generation_jobs WHERE id=?", (job_id,),
        ).fetchone()
        if job is None or not db.execute(
            "SELECT id FROM chat_sessions WHERE id=?", (session_id,),
        ).fetchone():
            raise EvidenceChanged("Chat was removed while the answer was generated")
        if job["state"] == "completed":
            return json.loads(job["result"])
        store.verify_sources(db, sources)
        db.execute(
            "INSERT INTO chat_messages "
            "(id,session_id,role,content,sources,created_at,retrieval_details) "
            "VALUES(?,?,?,?,?,?,?)",
            (job_id, session_id, "assistant", result["answer"], json.dumps(sources), now,
             json.dumps(result["retrieval_details"])),
        )
        store._save_evidence(db, job_id, result["evidence"], sources)
        db.executemany("INSERT OR IGNORE INTO generation_job_sources VALUES(?,?)", [
            (job_id, source["id"]) for source in sources
        ])
        db.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now, session_id))
        db.execute(
            "UPDATE generation_jobs SET state='completed',result=?,error=NULL WHERE id=?",
            (json.dumps(completed), job_id),
        )
    return completed


class ChatJobs:
    """Mixed into the existing generation queue so chats share its worker and outbox."""

    def prepare_chat(
        self, params: dict, db: Any, job_id: str,
        existing: Mapping | None, supplied: dict,
    ) -> dict:
        if db.execute("SELECT id FROM deleted_chat_jobs WHERE id=?", (job_id,)).fetchone():
            raise HTTPException(410, "Chat was removed")
        scope_supplied = bool(set(supplied) & set(SCOPE_FIELDS))
        if existing:
            previous = json.loads(existing["parameters"])
            params["session_id"] = params["session_id"] or previous.get("session_id")
            return params
        key = params["session_id"]
        old = (db.execute("SELECT * FROM chat_sessions WHERE id=?", (key,)).fetchone()
               if key else None)
        if key and old is None:
            raise HTTPException(404, "Chat session not found")
        if old is not None:
            if scope_supplied and any(
                old[field] != params[field] for field in SCOPE_FIELDS
            ):
                raise HTTPException(409, "Start a new chat to choose a different scope")
            pending = db.execute(
                "SELECT id FROM generation_jobs WHERE kind='chat' "
                "AND state IN ('queued','running') "
                "AND json_extract(parameters,'$.session_id')=?", (key,),
            ).fetchone()
            if pending:
                raise HTTPException(409, "A reply is already being prepared in this chat")
        else:
            self._validate_chat_scope(params)
            key = str(uuid4())
            now = stamp(utc_now())
            title = " ".join(params["question"].split())[:120]
            db.execute(
                "INSERT INTO chat_sessions "
                "(id,title,created_at,updated_at,day,meeting_id,start_day,end_day) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (key, title, now, now, *(params[field] for field in SCOPE_FIELDS)),
            )
        params["session_id"] = key
        now = stamp(utc_now())
        db.execute(
            "INSERT INTO chat_messages "
            "(id,session_id,role,content,sources,created_at,retrieval_details) "
            "VALUES(?,?,?,?,?,?,?)",
            (user_message_id(job_id), key, "user", params["question"], "[]", now, None),
        )
        db.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now, key))
        return params

    def _validate_chat_scope(self, params: dict) -> None:
        if params["meeting_id"] and not any(
            meeting["id"] == params["meeting_id"] for meeting in self.memory.timeline()["meetings"]
        ):
            raise HTTPException(404, "Meeting not found")
        if params["day"]:
            start, end = self.memory.day_window(date.fromisoformat(params["day"]))
            if not self.store.has_recordings(start, end) and not self.store.diary(params["day"]):
                raise HTTPException(404, "No diary or recordings found for this date")

    async def execute_chat(self, job: dict) -> dict:
        params = job["parameters"]
        self.chat_progress[job["id"]] = {"draft": "", "stage": "searching"}

        async def event(value: dict) -> None:
            progress = self.chat_progress[job["id"]]
            if value["type"] == "delta":
                progress["stage"] = "generating"
                progress["draft"] += value["text"]
            elif value["type"] == "status":
                progress["stage"] = value["stage"]

        try:
            return await self.memory.chat(
                params["question"], params["limit"], params["session_id"],
                date.fromisoformat(params["day"]) if params["day"] else None,
                params["meeting_id"], False,
                date.fromisoformat(params["start_day"]) if params["start_day"] else None,
                date.fromisoformat(params["end_day"]) if params["end_day"] else None,
                on_event=event, request_id=job["id"],
            )
        finally:
            self.chat_progress.pop(job["id"], None)

    def pending_chat(self, session_id: str) -> dict | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT id,state FROM generation_jobs WHERE kind='chat' "
                "AND json_extract(parameters,'$.session_id')=? "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if row and row["state"] in {"queued", "running", "failed"}:
            return self.get(row["id"])
        return None

    def delete_chat(self, session_id: str) -> bool:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT id FROM chat_sessions WHERE id=?", (session_id,)).fetchone():
                return False
            jobs = ("SELECT id FROM generation_jobs WHERE kind='chat' "
                    "AND json_extract(parameters,'$.session_id')=?")
            db.execute(f"INSERT OR IGNORE INTO deleted_chat_jobs {jobs}", (session_id,))
            db.execute(
                f"DELETE FROM notifications WHERE json_extract(target,'$.session_id')=? "
                f"OR json_extract(target,'$.job_id') IN ({jobs})", (session_id, session_id),
            )
            db.execute("DELETE FROM output_evidence WHERE id IN "
                       "(SELECT id FROM chat_messages WHERE session_id=?)", (session_id,))
            db.execute(f"DELETE FROM generation_job_sources WHERE job_id IN ({jobs})",
                       (session_id,))
            db.execute(f"DELETE FROM generation_jobs WHERE id IN ({jobs})", (session_id,))
            db.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
        return True
