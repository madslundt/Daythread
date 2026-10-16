"""User importance marks, independent of timestamped capture gestures."""

import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain import project_events, stamp, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS importance (
 kind TEXT NOT NULL, id TEXT NOT NULL, section TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, PRIMARY KEY(kind,id,section)
);
CREATE TRIGGER IF NOT EXISTS importance_source_delete AFTER DELETE ON audio BEGIN
 DELETE FROM importance WHERE kind='source' AND id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS importance_meeting_delete AFTER DELETE ON events BEGIN
 DELETE FROM importance WHERE kind='meeting' AND id=OLD.event_id;
END;
CREATE TRIGGER IF NOT EXISTS importance_label_delete AFTER DELETE ON entities BEGIN
 DELETE FROM importance WHERE kind='label' AND id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS importance_label_merge AFTER UPDATE OF merged_into ON entities
WHEN NEW.merged_into IS NOT NULL BEGIN
 INSERT OR IGNORE INTO importance SELECT kind,NEW.merged_into,section,created_at
 FROM importance WHERE kind='label' AND id=OLD.id;
 DELETE FROM importance WHERE kind='label' AND id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS importance_recap_delete AFTER DELETE ON diary_versions BEGIN
 DELETE FROM importance WHERE kind='recap' AND id=OLD.id;
END;
"""


class ImportanceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["source", "meeting", "label", "recap"]
    id: UUID
    section: str = Field(default="", max_length=20000)

    @model_validator(mode="after")
    def section_target(self):
        if self.section and (self.kind != "recap" or not self.section.strip()):
            raise ValueError("Provide nonempty section text from a saved recap")
        return self


class ImportanceEdit(ImportanceTarget):
    important: bool = Field(strict=True)


class ImportanceStore:
    def importance_marks(self) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT kind,id,section FROM importance ORDER BY created_at,kind,id,section"
                )
            ]

    def set_importance(self, body: ImportanceEdit) -> dict:
        key = (body.kind, str(body.id), body.section)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if body.kind == "meeting":
                events = [dict(row) for row in db.execute("SELECT * FROM events")]
                timeline = project_events(events, utc_now())
                exists = any(meeting["id"] == key[1] for meeting in timeline["meetings"])
            elif body.kind == "label":
                exists = db.execute(
                    "SELECT 1 FROM entities WHERE id=? AND merged_into IS NULL", (key[1],)
                ).fetchone()
            elif body.kind == "source":
                exists = db.execute("SELECT 1 FROM audio WHERE id=?", (key[1],)).fetchone()
            else:
                exists = db.execute(
                    "SELECT content FROM diary_versions WHERE id=?", (key[1],)
                ).fetchone()
                if exists and body.section:
                    summary = json.loads(exists["content"]).get("summary") or ""
                    section_text = " ".join(body.section.replace("**", "").split())
                    summary_text = " ".join(summary.replace("**", "").split())
                    if section_text not in summary_text:
                        raise HTTPException(409, "Recap section changed; reload before marking it")
            if not exists:
                raise HTTPException(404, "Item unavailable; refresh before marking it")
            if body.important:
                db.execute(
                    "INSERT OR IGNORE INTO importance(kind,id,section,created_at) VALUES(?,?,?,?)",
                    (*key, stamp(utc_now())),
                )
            else:
                db.execute("DELETE FROM importance WHERE kind=? AND id=? AND section=?", key)
        return body.model_dump(mode="json")


router = APIRouter(prefix="/v1/importance")


@router.get("")
def marks(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"items": request.app.state.memory.store.importance_marks()}


@router.put("")
def edit(request: Request, body: ImportanceEdit) -> dict:
    return request.app.state.memory.store.set_importance(body)
