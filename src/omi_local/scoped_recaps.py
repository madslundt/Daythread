"""Tag/place recaps select original speech before preparation and preserve full-day history."""

from .conversation_state import Conversation
from .domain import EvidenceChanged, stamp
from .recaps import DAY_PROMPT_VERSION, Recaps, digest, topic_sources
from .scoped_recap_cache import ScopedRecapCache
from .source_scope import matching_source_ids


class ScopedRecaps(Recaps):
    def __init__(self, memory, tag_id=None, zone_id=None):
        super().__init__(memory)
        self.scope = {"tag_id": str(tag_id) if tag_id else None,
                      "zone_id": str(zone_id) if zone_id else None}
        self.cache = ScopedRecapCache(memory.store)

    def group_key(self, group):
        return digest({"scope": self.scope, "conversation": super().group_key(group)})

    def snapshot(self, day, now, db=None):
        if db is None:
            with self.memory.store.connect() as connection:
                connection.execute("BEGIN")
                return self.snapshot(day, now, connection)
        selected = matching_source_ids(db, **self.scope)
        if selected is None:
            raise ValueError("A filtered recap requires a tag or location filter")
        start, end = map(stamp, self.memory.day_window(day))
        original = self.memory.recaps.groups(now, db)
        rows = self.memory.store.recap_snapshot(db)["rows"]
        members = [row for row in rows if row["id"] in selected
                   and row["started_at"] < end and row["ended_at"] > start]
        ids = {row["id"] for row in members}
        groups = []
        for group in original:
            sources = [row for row in group.sources if row["id"] in ids]
            if sources:
                groups.append(Conversation(
                    sources, group.closed_at if group.ready else None, group.basis,
                    (self.memory.recaps.group_key(group),), group.hints,
                ))
        definitions = {}
        for field, table in (("tag_id", "conversation_tags"), ("zone_id", "location_zones")):
            if self.scope[field]:
                definitions[field] = dict(db.execute(
                    f"SELECT * FROM {table} WHERE id=?", (self.scope[field],)
                ).fetchone())
        key = digest({
            "scope": self.scope, "definitions": definitions, "day": day.isoformat(),
            "members": [(row["id"], row["source_version"], row["transcript_version"], row["state"])
                        for row in members],
            "groups": [(self.group_key(group), group.ready) for group in groups],
            "prompt": DAY_PROMPT_VERSION, "version": "filtered-originals-1",
            "model": self.memory.settings.llm_route("summary").identity,
            "language": self.memory.settings.summary_language.lower(),
            "timezone": self.memory.settings.timezone,
        })
        return {"key": key, "groups": groups, "members": members}

    def check(self, day, now, expected, db=None):
        if self.snapshot(day, now, db)["key"] != expected:
            raise EvidenceChanged("Recording filter or selected evidence changed; retry the recap")

    def base(self, day, now, snapshot):
        start, end = map(stamp, self.memory.day_window(day))
        groups, members = snapshot["groups"], snapshot["members"]
        return {
            "start": start, "end": end, "day": day.isoformat(),
            "timezone": self.memory.settings.timezone, "scope": self.scope,
            "scope_fingerprint": snapshot["key"], "no_matching_sources": not members,
            "summary": None, "sources": [], "output_id": None, "evidence": None,
            "generation_job_id": None, "generation_state": None, "generation_error": None,
            "generated_at": None, "no_recap_content": bool(members) and not groups,
            "pending_chunks": sum(row["state"] in ("queued", "transcribing") for row in members),
            "audio_gaps": [], "capture_gaps": [], "markers": [], "meetings": [],
            "hint_timing": "approximate", "completeness": None,
            "stale": any(group.ready for group in groups), **self.status(groups),
        }

    def cached(self, day, now, groups=None):
        with self.memory.store.connect() as db:
            db.execute("BEGIN")
            snapshot = self.snapshot(day, now, db)
            base = self.base(day, now, snapshot)
            saved = self.cache.get("day:" + snapshot["key"], db)
        if saved is None:
            return base
        return saved | self.status(snapshot["groups"]) | {
            "stale": False, "scope": self.scope, "scope_fingerprint": snapshot["key"],
            "no_matching_sources": not snapshot["members"],
        }

    def group_summary(self, key):
        return self.cache.get("note:" + key)

    def save_group_summary(self, key, summary, sources, guard):
        self.cache.save("note:" + key, summary, sources, guard)

    async def prepare_topics(self, group):
        # Topic contexts may span excluded recordings. Filtered notes use only selected originals.
        return

    def note_sources(self, sources):
        return topic_sources([{**source, "sections": []} for source in sources])

    async def generate(self, day, now, expected_fingerprint=None):
        snapshot = self.snapshot(day, now)
        if expected_fingerprint and snapshot["key"] != expected_fingerprint:
            raise EvidenceChanged("Recording filter changed before recap generation; retry")
        cached = self.cache.get("day:" + snapshot["key"])
        if cached is not None:
            return self.cached(day, now)

        def guard(db):
            self.check(day, now, snapshot["key"], db)

        base = self.base(day, now, snapshot)
        if not any(group.ready for group in snapshot["groups"]):
            return base
        base["sources"] = snapshot["members"]
        result = await self.synthesize(day, now, snapshot["groups"], guard, base)
        result.update(scope=self.scope, scope_fingerprint=snapshot["key"], stale=False)
        self.cache.save("day:" + snapshot["key"], result, result["sources"], guard)
        return self.cached(day, now)
