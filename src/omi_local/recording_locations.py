"""Editable recording places, kept separate from immutable capture observations."""

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_locations (
 source_id TEXT PRIMARY KEY, location TEXT, version INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS location_source_deleted BEFORE DELETE ON audio BEGIN
 DELETE FROM source_locations WHERE source_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS location_source_expired AFTER UPDATE OF state ON audio
WHEN NEW.state='expired' BEGIN
 DELETE FROM source_locations WHERE source_id=NEW.id;
END;
"""


class SavedLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zone_id: UUID
    location_id: UUID


class LocationChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0, strict=True)
    location: SavedLocation | None


def saved_location(db, selection):
    zone = db.execute(
        "SELECT name,locations FROM location_zones WHERE id=?", (str(selection.zone_id),)
    ).fetchone()
    point = next((point for point in json.loads(zone["locations"])
                  if point["id"] == str(selection.location_id)), None) if zone else None
    if point is None:
        raise HTTPException(404, "Saved place unavailable; choose another saved place")
    # Preserve where the recording was assigned, even if this saved place moves later.
    return {"zone_id": str(selection.zone_id), "location_id": str(selection.location_id),
            "zone_name": zone["name"], "label": point["label"],
            "latitude": point["latitude"], "longitude": point["longitude"]}


def decorate_locations(db, rows):
    all_rows = [source for row in rows for source in [row, *row.get("alternate_sources", [])]]
    by_id = {row["id"]: row for row in all_rows}
    if by_id:
        for row in db.execute(
            "SELECT source_id,location FROM source_locations WHERE location IS NOT NULL "
            "AND source_id IN (SELECT value FROM json_each(?))",
            (json.dumps(list(by_id)),),
        ):
            by_id[row["source_id"]]["manual_location"] = json.loads(row["location"])
    return rows


class RecordingLocationStore:
    def recording_location(self, source_id, change=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE" if change else "BEGIN")
            source = db.execute(
                "SELECT source_metadata FROM audio WHERE id=? AND state!='expired'", (source_id,)
            ).fetchone()
            if source is None:
                raise HTTPException(404, "Source unavailable")
            row = db.execute(
                "SELECT * FROM source_locations WHERE source_id=?", (source_id,)
            ).fetchone()
            version = row["version"] if row else 0
            manual = json.loads(row["location"]) if row and row["location"] else None
            if change is not None:
                if change.expected_version != version:
                    raise HTTPException(409, "Location changed; reload before saving")
                manual = saved_location(db, change.location) if change.location else None
                version += 1
                db.execute(
                    "INSERT INTO source_locations VALUES(?,?,?) ON CONFLICT(source_id) "
                    "DO UPDATE SET location=excluded.location,version=excluded.version",
                    (source_id, json.dumps(manual) if manual else None, version),
                )
            original = json.loads(source["source_metadata"]).get("location")
            return {"version": version, "location": manual or original,
                    "original_location": original, "manual": manual is not None}


def recording_location_routes(memory):
    router = APIRouter(prefix="/v1/sources")

    @router.get("/{source_id}/location")
    def get(source_id: UUID, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return memory().store.recording_location(str(source_id))

    @router.put("/{source_id}/location")
    def put(source_id: UUID, body: LocationChange):
        return memory().store.recording_location(str(source_id), body)

    return router
