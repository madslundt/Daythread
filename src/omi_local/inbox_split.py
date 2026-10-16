"""Reduce extraction windows after truncated output without publishing partial results."""

import hashlib
import json
from uuid import uuid4

from .domain import stamp, utc_now


def split_job(db, job: dict) -> bool:
    inputs = json.loads(job["inputs"])
    primary = inputs[0]
    if primary["end"] - primary["start"] <= 1200:
        return False
    middle = (primary["start"] + primary["end"]) // 2
    for low, high in [(primary["start"], middle + 150), (middle - 150, primary["end"])]:
        parts = [primary | {"start": low, "end": high}, *inputs[1:]]
        digest = hashlib.sha256(json.dumps([job["fingerprint"], parts], sort_keys=True).encode())
        key = str(uuid4())
        inserted = db.execute(
            "INSERT OR IGNORE INTO inbox_jobs "
            "(id,fingerprint,inputs,timezone,model,prompt_version,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                key,
                digest.hexdigest(),
                json.dumps(parts),
                job["timezone"],
                job["model"],
                job["prompt_version"],
                stamp(utc_now()),
            ),
        ).rowcount
        if inserted:
            db.executemany(
                "INSERT INTO inbox_dependencies VALUES(?,?,?)",
                [(key, part["id"], part["revision"]) for part in parts],
            )
    db.execute(
        "UPDATE inbox_jobs SET state='split',claim=NULL,error=NULL WHERE id=? AND claim=?",
        (job["id"], job["claim"]),
    )
    return True
