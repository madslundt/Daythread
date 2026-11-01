"""A persisted fair share for live capture and the oldest deferred audio.

At most three recent live chunks may pass an eligible historical/import job.
The streak is stored with each claim so restarting cannot starve the backlog.
The fifteen-minute live window is a priority window, not a capture boundary.
"""

from datetime import timedelta

from .domain import stamp

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcription_schedule (
 id INTEGER PRIMARY KEY, recent_streak INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO transcription_schedule(id,recent_streak) VALUES(1,0);
"""
RECENT_MINUTES = 15
MAX_RECENT_BURST = 3


def claim_audio(db, now):
    cutoff = stamp(now - timedelta(minutes=RECENT_MINUTES))
    due = stamp(now)
    # File imports and deferred live audio share the historical lane. A newly
    # uploaded old recording must not pretend to be current capture.
    live = "source_kind='' AND ended_at>=? AND started_at<=?"
    candidates = {}
    for lane, predicate in (("recent", live), ("history", f"NOT ({live})")):
        candidates[lane] = db.execute(
            "SELECT * FROM audio WHERE state='queued' AND retry_at<=? AND "
            + predicate + " ORDER BY started_at,id LIMIT 1",
            (due, cutoff, due),
        ).fetchone()
    streak = db.execute(
        "SELECT recent_streak FROM transcription_schedule WHERE id=1"
    ).fetchone()[0]
    lane = "recent" if candidates["recent"] and (
        not candidates["history"] or streak < MAX_RECENT_BURST
    ) else "history"
    row = candidates[lane]
    if row is None:
        return None
    db.execute(
        "UPDATE transcription_schedule SET recent_streak=? WHERE id=1",
        (min(streak + 1, MAX_RECENT_BURST) if lane == "recent" else 0,),
    )
    db.execute(
        "UPDATE audio SET state='transcribing',attempts=attempts+1,"
        "processing_started_at=? WHERE id=?", (due, row["id"]),
    )
    return dict(row)
