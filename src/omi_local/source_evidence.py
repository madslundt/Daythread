"""Suppress redundant model evidence while preserving the authoritative source archive."""

SCHEMA = """
CREATE INDEX IF NOT EXISTS source_matches_right ON source_matches(right_id,left_id);
CREATE VIEW IF NOT EXISTS canonical_audio AS
SELECT a.* FROM audio a
WHERE NOT (
 a.state='done' AND length(trim(coalesce(a.text,'')))>0 AND (
  EXISTS (
   SELECT 1 FROM audio earlier
   WHERE earlier.rowid<a.rowid AND earlier.state='done' AND earlier.text=a.text
    AND earlier.digest=a.digest
    AND earlier.started_at=a.started_at AND earlier.ended_at=a.ended_at
    AND earlier.source_kind NOT IN ('omi_api','omi_audio_webhook')
    AND a.source_kind NOT IN ('omi_api','omi_audio_webhook')
  ) OR EXISTS (
   SELECT 1 FROM source_matches m JOIN audio earlier
    ON earlier.id=CASE WHEN m.left_id=a.id THEN m.right_id ELSE m.left_id END
   WHERE (m.left_id=a.id OR m.right_id=a.id)
    AND earlier.rowid<a.rowid AND earlier.state='done' AND earlier.text=a.text
    AND earlier.started_at<=a.started_at AND earlier.ended_at>=a.ended_at
    AND earlier.source_kind!='omi_audio_webhook' AND a.source_kind!='omi_audio_webhook'
  )
 )
);
"""
