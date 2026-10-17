# Conversation locations and nearby reminders

On iPhone, open **Settings → Capture & uploads → Save conversation locations** to opt in.
Grant location access when asked. This preference is saved on that phone and defaults to off.

While a pendant is connected and capture is enabled, Daythread requests phone locations at
roughly 100-metre accuracy with a 100-metre distance filter. iOS can keep updates running during
an active background capture session and may show its location indicator. Pausing capture,
disconnecting the pendant, clearing the active profile, or disabling the preference stops updates.
Capture continues if GPS is unavailable or access is declined. Background updates are subject to
iOS suspension, permission, and power policies; uninterrupted location coverage is not promised.

Each new audio chunk can store one observation: latitude, longitude, horizontal accuracy in metres,
and the original observation time. An observation must be no more than 120 seconds old at the
chunk's start, with at most five seconds of clock tolerance, and accuracy must be within 1,000 metres.
This is a point near the start of the recording, not a movement trace or an exact conversation venue.
Existing recordings and imported audio do not acquire inferred GPS coordinates.

The durable phone outbox preserves the observation before networking. Retries use the same audio
ID, capture time and location; a different observation under the same ID is rejected. Locations sync
to the selected private archive with the audio and appear in source details on web and iPhone,
including accuracy and observation time. Opening Maps sends that coordinate to Apple. Locations
are included in archive backups, disappear on recording deletion, and are removed from the active
archive when transcript retention expires, even if the audio had already expired. Earlier backups
retain their own copies. Disabling capture locations affects subsequent observations.

## Remind me near a discussed place

For an active decision with current transcript evidence, follow-up extraction can suggest a
place-bound errand or an explicit arrival/departure reminder without inventing a due date.
The suggested location must be an exact place phrase in a validated original quotation. Incidental
place mentions should not produce a reminder. The model supplies no coordinates; the phone's
recorded location is never substituted for a place mentioned in speech.

For example, a recorded decision to “pick up the bike at Nørrebro Cykler when nearby” can offer
**When nearby · Nørrebro Cykler** in the decision's follow-ups, even without a deadline.

1. Tap the nearby follow-up on iPhone.
2. Search for the place or address and select the correct result, or choose **Use current location**.
   Searches and map previews use Apple Maps. Current location requests permission only when selected.
3. Check the displayed place/map and choose a radius of 100, 200, 500 or 1,000 metres.
4. Tap **Save to Reminders** and grant Reminders access if needed.

Daythread checks the decision and its source evidence before and after permission prompts, then
saves an `EKReminder` with an `EKStructuredLocation` and an arrival/departure `EKAlarm`.
A known deadline is preserved separately. Notes retain the decision ID and version. No calendar
invitation, message, or reminder is created merely by generating a suggestion.

Apple Reminders owns the saved reminder and location alert, including while Daythread is closed
or home-core is unreachable. Enable notifications and location alerts for Reminders in iPhone
Settings. Trigger timing and distance are approximate and depend on system conditions. Manage,
complete, or remove a saved reminder in Reminders. Later transcript changes do not retract items
already saved in external apps. Saving a nearby reminder does not enable conversation-location
capture. Reopening a decision can offer the action again; saved-state hints are session-local.

Calendar events continue to carry the discussed location into Apple's native calendar editor,
where the owner can choose or change it. The web app displays nearby suggestions with an iPhone
handoff. Portable `.ics` export rejects proximity reminders because it cannot guarantee a location
alert; it must not silently turn one into an ordinary undated task.

## API

`PUT /v1/audio/{id}` accepts four optional query fields together for native phone capture:
`latitude`, `longitude`, `horizontal_accuracy`, and `location_captured_at` (offset-aware ISO time).
Partial, out-of-range, non-finite, stale, inaccurate, and manual-import observations are rejected.
The source response exposes the normalized observation in `source_metadata.location` with keys
`latitude`, `longitude`, `horizontal_accuracy`, and `captured_at`.

Decision follow-up responses add optional `location_trigger`: `arrive`, `leave`, or `null`.
A non-null trigger requires a reminder and a nonempty location; generated proposals additionally
require the exact cited place phrase. Existing clients may ignore the optional field; update clients
and backend together to use proximity reminders.

## Validation and device acceptance

Automated coverage checks timestamp/accuracy bounds, backward-compatible outbox recovery,
offline retries, conflicting retries, source reads, profile isolation, deletion, transcript retention,
grounded undated follow-ups, invalid generated places, and refusing misleading ICS exports.
Swift core tests and an iOS Simulator build check the native contracts and integration.

Still verify on a physical iPhone: capture with the screen locked, pause/disconnect/reconnect,
denied/revoked/reduced-accuracy permission, offline uploads after movement, Reminders permissions,
arrival and departure while Daythread is closed, and notification/location-alert settings. These
checks need actual device movement and are not established by a simulator build.

Apple references: [location-based reminders](https://developer.apple.com/documentation/eventkit/managing-location-based-reminders),
[setting an alarm](https://developer.apple.com/documentation/eventkit/setting-an-alarm), and
[background location updates](https://developer.apple.com/documentation/corelocation/handling-location-updates-in-the-background).

## Named location zones

Create zones under **Settings → Location zones** on web or iPhone. A zone can be named Home,
Work, Gym, or any other name. Each zone contains one or more named locations; every location has
its own center and radius (10–100,000 metres). For example, Work can contain a main office with a
150-metre radius and a second office with a 500-metre radius. A profile supports up to 100 zones,
with up to 30 locations per zone. Definitions are saved in that profile's archive and included in
archive backups.

On iPhone, add a location by searching Apple Maps, using current GPS, entering coordinates, or
choosing **Save as zone location** from a recording's location details. Check the map and set the
radius before saving. On web, use current GPS, enter coordinates, or reuse a recording's location
with the same action. Browser GPS depends on browser permission and secure-context availability;
manual coordinates and recording locations work without granting browser location access.
Apple Maps is used for native place searches and map previews. No server geocoder is contacted.

Today and historical day views offer a **Location zone** filter for recordings and their transcripts.
Expanded recording groups and later pages retain that filter. Select **All locations** to include
recordings without GPS. The filter applies to recording evidence; the day recap remains a recap
of the whole day and is labeled accordingly when a filter is selected.

Membership is derived from whether a recording's original GPS point falls within *any* of a zone's
circles. Overlapping zones can both match. Accuracy is retained on the source and is not added to
the radius; a GPS reading is approximate, particularly near a boundary. No place is inferred for
recordings without GPS. Moving a center or changing a radius immediately changes how historical
recordings match on their next query, while original observations remain untouched. Deleting a
zone removes only its definition, preserving recordings, transcripts, and their GPS observations.
Saved zones are independent of transcript retention and remain until explicitly removed. Already
saved Apple Reminders geofences are independent of later zone edits.

The profile-scoped API supports `GET /v1/location-zones`, `PUT /v1/location-zones/{id}` and
`DELETE /v1/location-zones/{id}?expected_version=N`. PUT receives `name`, `expected_version`
(`0` for a new UUID), and a `locations` array with each location's `id`, `label`, `latitude`,
`longitude`, and `radius_m`. Writes increment `version`; stale edits/deletes and duplicate names
are rejected. Raw coordinates and transcripts are not rewritten when zones change.

`GET /v1/transcripts`, `GET /v1/recording-groups`, and
`GET /v1/recording-groups/{id}/sources` accept an optional `zone_id` UUID. Filtering precedes
counting, grouping, deduplication, and pagination. Unknown, deleted, or other-profile zones return
404 instead of silently dropping the filter. The matching function is shared so future summary,
recall, and reminder features can apply the same definitions without copying location records.
