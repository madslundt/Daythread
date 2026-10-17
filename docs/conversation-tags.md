# Conversation tags

Tags are manual categories such as Family, Planning, or Training. They are independent from
recorded GPS coordinates, named location zones, people/project labels, and model-generated topics.
A tag named Home does not move a conversation into the Home zone or change its recorded location.

On web, expand a recording or recording group, then open **Tags**. On iPhone, open a recording's
**Conversation tags**, or expand a recording group and choose **Conversation tags**. Create a tag
or select an existing one; remove a tag using its remove action. Tags can be added without GPS,
a transcript, or a running model. A profile has its own reusable tag vocabulary.

The day timeline has separate **Tag** and **Location zone** filters. They work together: selecting
Training and Work returns tagged recordings whose original GPS points match Work. Expanded groups,
transcript results, counts, and later pages retain both filters. Full-day recaps continue to cover
the whole day; these filters do not regenerate or narrow summaries.

## Scope and lifetime

A recording tag is attached to a stable source UUID. A conversation-group edit applies to all
currently saved, non-expired recordings in that day's unfiltered browsing group, including members
hidden by text, tag, location, or page filters and alternative uploads collapsed into one row.
The editor shows the number of recordings affected.
Browsing groups are approximate time-based sessions, not proof of a single real conversation.

Tag assignments stay with the original source IDs when groups later split, merge, or change their
first recording. New recordings arriving later do not automatically inherit a category. If only
part of the current group has a tag, the editor shows its coverage and offers **Apply to all**.
A membership fingerprint and tag revision reject saves when the preview is stale; reload before
retrying. Assignment changes are incremental and never replace unrelated tags.

Tags survive transcript corrections. Recording deletion and transcript expiry remove that
recording's assignments. Audio-only expiry keeps tags when the transcript remains. Unused names
stay in the profile's tag directory for reuse. Archive backups include tag names and assignments.
Tag changes do not rewrite transcripts, source versions, capture metadata, upload receipts, GPS
observations, or zone definitions. They do not create calendar events or location alerts.

Names are trimmed, Unicode-normalized, and matched case-insensitively for reuse; duplicate names
reuse their existing tag ID. Tags support up to 80 characters, with 50 tags per recording and
1,000 reusable names per profile. A conversation edit handles up to 5,000 saved recordings.

## API

- `GET /v1/tags`: reusable tag directory with revision and assignment counts.
- `GET /v1/sources/{id}/tags`: recording tag selection and membership fingerprint.
- `PUT /v1/sources/{id}/tags`: incrementally add or remove a recording tag.
- `GET /v1/recording-groups/{id}/tags?day=YYYY-MM-DD`: complete group selection.
- `PUT /v1/recording-groups/{id}/tags?day=YYYY-MM-DD`: incrementally edit that group.

PUT supplies `expected_revision`, `expected_membership`, and `action` (`add` or `remove`). Add uses
either `tag_id` or a new `name`; remove requires `tag_id`. Stale revisions/membership return 409.
Missing/expired recordings and other-profile tags are rejected. All routes use the selected archive.

`GET /v1/transcripts`, `GET /v1/recording-groups`, and group source expansion accept `tag_id` alongside
`zone_id` and text/date parameters. Filters apply before grouping, counting, deduplication, and
pagination. Recording source responses and browsing groups expose assigned `tags` for display.
The source response omits empty tag lists for backward compatibility.
