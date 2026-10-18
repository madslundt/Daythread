"""Owner-defined place groups; membership is derived from original GPS observations."""

import json
import math
import sqlite3
from functools import lru_cache
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA = """
CREATE TABLE IF NOT EXISTS location_zones (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
 locations TEXT NOT NULL, version INTEGER NOT NULL
);
"""


class ZoneLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: UUID = Field(default_factory=uuid4)
    label: str = Field(min_length=1, max_length=100)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: float = Field(ge=10, le=100000)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value):
        return clean_name(value)


def clean_name(value: str) -> str:
    value = value.strip()
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Use a nonempty name without control characters")
    return value


class ZoneWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    locations: list[ZoneLocation] = Field(min_length=1, max_length=30)
    expected_version: int = Field(ge=0, strict=True)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        return clean_name(value)

    @field_validator("locations")
    @classmethod
    def unique_ids(cls, value):
        if len({p.id for p in value}) != len(value):
            raise ValueError("Location IDs must be unique within a zone")
        return value


@lru_cache(maxsize=128)
def centers(encoded):
    return tuple((p["latitude"], p["longitude"], p["radius_m"]) for p in json.loads(encoded))


def contains(latitude, longitude, encoded):
    if latitude is None or longitude is None:
        return 0
    for lat, lon, radius in centers(encoded):
        a, b = math.radians(latitude), math.radians(lat)
        hav = (
            math.sin((b - a) / 2) ** 2
            + math.cos(a) * math.cos(b) * math.sin(math.radians(lon - longitude) / 2) ** 2
        )
        distance = 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, hav))))
        if distance <= radius + 0.000001:
            return 1
    return 0


def zone_filter(db, zone_id: str | None) -> tuple[str, list]:
    if not zone_id:
        return "1=1", []
    row = db.execute("SELECT locations FROM location_zones WHERE id=?", (zone_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Location zone unavailable; choose another zone")
    return (
        "location_zone_contains("
        "coalesce((SELECT json_extract(location,'$.latitude') FROM source_locations "
        "WHERE source_id=audio.id),json_extract(source_metadata,'$.location.latitude')),"
        "coalesce((SELECT json_extract(location,'$.longitude') FROM source_locations "
        "WHERE source_id=audio.id),json_extract(source_metadata,'$.location.longitude')),?)=1",
        [row["locations"]],
    )


def public_zone(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "locations": json.loads(row["locations"]),
    }


class LocationZoneStore:
    def location_zones(self):
        with self.connect() as db:
            return [
                public_zone(row)
                for row in db.execute("SELECT * FROM location_zones ORDER BY name_key,id")
            ]

    def save_location_zone(self, key, body: ZoneWrite):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM location_zones WHERE id=?", (key,)).fetchone()
            version = old["version"] if old else 0
            if version != body.expected_version:
                raise HTTPException(409, "Zone changed; reload before saving")
            if not old and db.execute("SELECT count(*) FROM location_zones").fetchone()[0] >= 100:
                raise HTTPException(422, "An archive supports up to 100 location zones")
            try:
                db.execute(
                    "INSERT INTO location_zones VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "name=excluded.name,name_key=excluded.name_key,"
                    "locations=excluded.locations,version=excluded.version",
                    (
                        key,
                        body.name,
                        body.name.casefold(),
                        json.dumps([point.model_dump(mode="json") for point in body.locations]),
                        version + 1,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "A zone with this name already exists") from exc
            centers.cache_clear()
            return public_zone(
                db.execute("SELECT * FROM location_zones WHERE id=?", (key,)).fetchone()
            )

    def delete_location_zone(self, key, version):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version FROM location_zones WHERE id=?", (key,)).fetchone()
            if row is None:
                raise HTTPException(404, "Location zone unavailable")
            if row["version"] != version:
                raise HTTPException(409, "Zone changed; reload before deleting")
            db.execute("DELETE FROM location_zones WHERE id=?", (key,))
            centers.cache_clear()


def location_zone_routes(memory):
    router = APIRouter(prefix="/v1/location-zones")

    @router.get("")
    def directory(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {"items": memory().store.location_zones()}

    @router.put("/{zone_id}")
    def save(zone_id: UUID, body: ZoneWrite):
        return memory().store.save_location_zone(str(zone_id), body)

    @router.delete("/{zone_id}", status_code=204)
    def delete(zone_id: UUID, expected_version: int = Query(ge=1)):
        memory().store.delete_location_zone(str(zone_id), expected_version)
        return Response(status_code=204)

    return router
