"""Shared tag/place source membership, independent of pagination or inferred content."""

from .conversation_tags import tag_filter
from .location_zones import zone_filter


def matching_source_ids(db, tag_id=None, zone_id=None) -> set[str] | None:
    if not tag_id and not zone_id:
        return None
    tag, tag_args = tag_filter(db, str(tag_id) if tag_id else None)
    zone, zone_args = zone_filter(db, str(zone_id) if zone_id else None)
    return {
        row["id"]
        for row in db.execute(
            "SELECT id FROM audio WHERE state!='expired' AND (" + tag + ") AND (" + zone + ")",
            [*tag_args, *zone_args],
        )
    }
