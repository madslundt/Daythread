"""Manual conversation categories tied to source IDs, independent of location evidence."""

import hashlib
import json
import unicodedata
from datetime import date
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .domain import stamp
from .source_matching import MANUAL_UPLOAD_SQL

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_tags (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS source_tags (
 source_id TEXT NOT NULL, tag_id TEXT NOT NULL, PRIMARY KEY(source_id,tag_id)
);
CREATE INDEX IF NOT EXISTS source_tags_tag ON source_tags(tag_id,source_id);
CREATE TABLE IF NOT EXISTS tag_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 revision TEXT NOT NULL);
INSERT OR IGNORE INTO tag_state VALUES(1,lower(hex(randomblob(16))));
CREATE TRIGGER IF NOT EXISTS tags_source_deleted BEFORE DELETE ON audio
WHEN EXISTS(SELECT 1 FROM source_tags WHERE source_id=OLD.id) BEGIN
 DELETE FROM source_tags WHERE source_id=OLD.id;
 UPDATE tag_state SET revision=lower(hex(randomblob(16)));
END;
CREATE TRIGGER IF NOT EXISTS tags_source_expired AFTER UPDATE OF state ON audio
WHEN NEW.state='expired' AND EXISTS(SELECT 1 FROM source_tags WHERE source_id=NEW.id) BEGIN
 DELETE FROM source_tags WHERE source_id=NEW.id;
 UPDATE tag_state SET revision=lower(hex(randomblob(16)));
END;
"""


class TagChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_membership: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: str = Field(pattern=r"^(add|remove)$")
    tag_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def tag_name(cls, value):
        if value is None:
            return None
        if any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("Tag names must not contain control characters")
        value = " ".join(unicodedata.normalize("NFC", value).split())
        if not value:
            raise ValueError("Enter a tag name")
        return value

    @model_validator(mode="after")
    def target(self):
        if (self.tag_id is None) == (self.name is None) or (
            self.action == "remove" and self.tag_id is None
        ):
            raise ValueError("Choose one existing tag or enter a name when adding a tag")
        return self


def decorate_tags(db, rows):
    all_rows = []
    for row in rows:
        all_rows.append(row)
        all_rows.extend(row.get("alternate_sources", []))
    by_id = {row["id"]: [] for row in all_rows}
    if by_id:
        for row in db.execute(
            "SELECT s.source_id,t.id,t.name FROM source_tags s "
            "JOIN conversation_tags t ON t.id=s.tag_id "
            "WHERE s.source_id IN (SELECT value FROM json_each(?)) ORDER BY t.name_key,t.id",
            (json.dumps(list(by_id)),),
        ):
            by_id[row["source_id"]].append({"id": row["id"], "name": row["name"]})
    for row in all_rows:
        row["tags"] = by_id[row["id"]]
    return rows


def tag_filter(db, tag_id):
    if not tag_id:
        return "1=1", []
    if not db.execute("SELECT 1 FROM conversation_tags WHERE id=?", (tag_id,)).fetchone():
        raise HTTPException(404, "Tag unavailable; choose another tag")
    return (
        "EXISTS(SELECT 1 FROM source_tags st WHERE st.source_id=audio.id AND st.tag_id=?)",
        [tag_id],
    )


def members(db, anchor, start=None, end=None):
    if start is None:
        row = db.execute("SELECT id FROM audio WHERE id=? AND state!='expired'", (anchor,))
        if not row.fetchone():
            raise HTTPException(404, "Recording unavailable for tagging")
        return [anchor]
    from .recording_groups import group_recordings

    rows = [
        dict(row)
        for row in db.execute(
            "SELECT id,device_id,started_at,ended_at,state,source_kind,source_metadata,"
            "length(trim(coalesce(text,'')))>0 AS has_text,"
            "CASE WHEN " + MANUAL_UPLOAD_SQL + " THEN 'audio:' || digest || ':' || "
            "coalesce(json_extract(source_metadata,'$.part_index'),0) "
            "ELSE 'source:' || id END AS upload_key FROM audio "
            "WHERE started_at<? AND ended_at>? ORDER BY started_at,id",
            (stamp(end), stamp(start)),
        )
    ]
    # Match the timeline's upload deduplication before grouping. An alternative
    # upload's timestamp must not bridge otherwise separate conversations.
    canonical, uploads = {}, {}
    for row in rows:
        canonical.setdefault(row["upload_key"], row)
        uploads.setdefault(row["upload_key"], []).append(row)
    for group in group_recordings(list(canonical.values())):
        represented = [source for row in group["members"] for source in uploads[row["upload_key"]]]
        if any(row["id"] == anchor for row in represented):
            ids = sorted(row["id"] for row in represented if row["state"] != "expired")
            if ids:
                return ids
    raise HTTPException(404, "Conversation unavailable for tagging")


def tag_view(db, ids):
    return {
        "revision": db.execute("SELECT revision FROM tag_state").fetchone()[0],
        "membership": hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest(),
        "source_count": len(ids),
        "tags": [
            dict(row)
            for row in db.execute(
                "SELECT t.id,t.name,count(*) AS source_count FROM source_tags s "
                "JOIN conversation_tags t ON t.id=s.tag_id "
                "WHERE s.source_id IN (SELECT value FROM json_each(?)) "
                "GROUP BY t.id ORDER BY t.name_key,t.id",
                (json.dumps(ids),),
            )
        ],
    }


class ConversationTagStore:
    def tag_directory(self):
        with self.connect() as db:
            db.execute("BEGIN")
            return {
                "revision": db.execute("SELECT revision FROM tag_state").fetchone()[0],
                "items": [
                    dict(row)
                    for row in db.execute(
                        "SELECT t.id,t.name,count(s.source_id) AS source_count "
                        "FROM conversation_tags t LEFT JOIN source_tags s ON s.tag_id=t.id "
                        "GROUP BY t.id ORDER BY t.name_key,t.id"
                    )
                ],
            }

    def conversation_tags(self, anchor, start=None, end=None, change=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE" if change else "BEGIN")
            ids = members(db, anchor, start, end)
            view = tag_view(db, ids)
            if change is None:
                return view
            if view["revision"] != change.expected_revision:
                raise HTTPException(409, "Tags changed; reload before editing")
            if view["membership"] != change.expected_membership:
                raise HTTPException(409, "Conversation recordings changed; reload before tagging")
            if len(ids) > 5000:
                raise HTTPException(422, "Tag a smaller selection of recordings")
            key = str(change.tag_id) if change.tag_id else None
            if change.name is not None:
                row = db.execute(
                    "SELECT id FROM conversation_tags WHERE name_key=?", (change.name.casefold(),)
                ).fetchone()
                if row:
                    key = row["id"]
                else:
                    if db.execute("SELECT count(*) FROM conversation_tags").fetchone()[0] >= 1000:
                        raise HTTPException(422, "An archive supports up to 1000 tags")
                    key = str(uuid4())
                    db.execute(
                        "INSERT INTO conversation_tags VALUES(?,?,?)",
                        (key, change.name, change.name.casefold()),
                    )
            elif not db.execute("SELECT 1 FROM conversation_tags WHERE id=?", (key,)).fetchone():
                raise HTTPException(404, "Tag unavailable; choose another tag")
            encoded = json.dumps(ids)
            if change.action == "add":
                if db.execute(
                    "SELECT source_id FROM source_tags "
                    "WHERE source_id IN (SELECT value FROM json_each(?)) "
                    "GROUP BY source_id HAVING count(*)>=50 AND sum(tag_id=?)=0 LIMIT 1",
                    (encoded, key),
                ).fetchone():
                    raise HTTPException(422, "A recording supports up to 50 tags")
                db.executemany(
                    "INSERT OR IGNORE INTO source_tags VALUES(?,?)",
                    [(source, key) for source in ids],
                )
            else:
                db.execute(
                    "DELETE FROM source_tags WHERE tag_id=? AND source_id IN "
                    "(SELECT value FROM json_each(?))",
                    (key, encoded),
                )
            db.execute("UPDATE tag_state SET revision=?", (uuid4().hex,))
            return tag_view(db, ids)


def conversation_tag_routes(memory):
    router = APIRouter(prefix="/v1")

    @router.get("/tags")
    def directory(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return memory().store.tag_directory()

    @router.get("/sources/{source_id}/tags")
    def source_tags(source_id: UUID, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return memory().store.conversation_tags(str(source_id))

    @router.put("/sources/{source_id}/tags")
    def change_source(source_id: UUID, body: TagChange):
        return memory().store.conversation_tags(str(source_id), change=body)

    @router.get("/recording-groups/{group_id}/tags")
    def group_tags(group_id: UUID, day: date, response: Response):
        response.headers["Cache-Control"] = "no-store"
        start, end = memory().day_window(day)
        return memory().store.conversation_tags(str(group_id), start, end)

    @router.put("/recording-groups/{group_id}/tags")
    def change_group(group_id: UUID, day: date, body: TagChange):
        start, end = memory().day_window(day)
        return memory().store.conversation_tags(str(group_id), start, end, body)

    return router
