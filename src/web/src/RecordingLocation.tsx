import { useEffect, useId, useRef, useState } from "react";
import { ApiError, errorMessage, request } from "./api";
import { timeLabel } from "./format";
import { t } from "./i18n";
import { LocationZones, type LocationZone, type ZoneDirectory } from "./LocationZones";
import { Icon } from "./Icon";
import { profileScope } from "./profiles";
import type { Source } from "./types";
import { ErrorState, Loading } from "./UI";
import { useResource } from "./useResource";

type SavedPlaceReference = { zone_id: string; location_id: string };
type ManualLocation = NonNullable<Source["manual_location"]>;
type LocationSelection = {
  version: number;
  location: ManualLocation | NonNullable<NonNullable<Source["source_metadata"]>["location"]> | null;
  original_location: NonNullable<Source["source_metadata"]>["location"] | null;
  manual: boolean;
};

export function RecordingLocation({ source, timezone, onChanged }: { source: Source; timezone: string; onChanged?: () => void }) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<LocationSelection>();
  const trigger = useRef<HTMLButtonElement>(null);
  const editorID = useId();
  const wasOpen = useRef(false);
  // Unrelated source refreshes (for example tags) must not undo the visible save.
  const sourceLocation = JSON.stringify([source.manual_location, source.source_metadata?.location]);
  useEffect(() => setSaved(undefined), [source.id, sourceLocation]);
  useEffect(() => setOpen(false), [source.id]);
  useEffect(() => {
    if (!open && wasOpen.current) trigger.current?.focus({ preventScroll: true });
    wasOpen.current = open;
  }, [open]);
  const location = saved ? saved.location : source.manual_location ?? source.source_metadata?.location;
  const manual = saved ? saved.manual : Boolean(source.manual_location);
  const savedPlace = location && "zone_id" in location && Boolean(location.zone_id);
  function close() { setOpen(false); }
  return <section className="recording-location" aria-label={t("Conversation location")}>
    <div className="recording-metadata-row">
      <strong className="recording-metadata-label"><Icon name="location" size={17} />{t("Location")}</strong>
      {location ? <div className="recording-place-summary">
        <div className="recording-place-details">
          <strong>{"label" in location ? location.label : t("Captured location")}</strong>
          <span className="caption">{"zone_name" in location && location.zone_name ? `${location.zone_name} · ` : ""}
            {manual ? (savedPlace ? t("Saved place") : t("Manually set location")) : `${location.latitude.toFixed(5)}, ${location.longitude.toFixed(5)}`}
          </span>
        </div>
        <a className="text-link recording-place-map" href={`https://maps.apple.com/?ll=${location.latitude},${location.longitude}`}
          target="_blank" rel="noreferrer">{t("Open in Maps")}</a>
      </div> : <span className="caption recording-location-empty">{t("No location")}</span>}
      <button ref={trigger} type="button" className="text-button recording-metadata-action"
        disabled={source.state === "expired" || open} aria-expanded={open} aria-controls={editorID} onClick={() => setOpen(true)}>
        {location ? t("Edit location") : t("Add location")}
      </button>
    </div>
    {location && !manual ? <details className="recording-location-info"><summary>{t("Location details")}</summary>
      {"horizontal_accuracy" in location ? <p className="caption">±{Math.round(location.horizontal_accuracy)} {t("m")}
        {"captured_at" in location ? ` · ${timeLabel(location.captured_at, timezone)}` : ""}</p> : null}
      <p className="caption recording-location-provenance">
        {t("Phone location near the start of this recording. It does not identify places mentioned in the conversation.")}{" "}
        <a href={`/settings/locations?latitude=${location.latitude}&longitude=${location.longitude}`}>{t("Save as zone location")}</a>
      </p></details> : null}
    {open ? <div id={editorID}><RecordingLocationEditor sourceID={source.id} onCancel={close} onSaved={(result) => {
      setSaved(result); close(); onChanged?.(); window.dispatchEvent(new Event("omi:locations-changed"));
    }} /></div> : null}
  </section>;
}

export function SavedPlaceChoices({ zones, selected, disabled, onChoose }: {
  zones: LocationZone[]; selected?: Partial<SavedPlaceReference>; disabled: boolean;
  onChoose: (place: SavedPlaceReference) => void;
}) {
  const [query, setQuery] = useState("");
  const count = zones.reduce((total, zone) => total + zone.locations.length, 0);
  const search = query.trim().toLocaleLowerCase();
  const matching = zones.map((zone) => ({ ...zone, locations: zone.locations.filter((point) =>
    `${zone.name} ${point.label}`.toLocaleLowerCase().includes(search),
  ) })).filter((zone) => zone.locations.length);
  return <div className="saved-place-choices">
    {count > 5 ? <label className="saved-place-search"><span className="sr-only">{t("Search saved places")}</span>
      <Icon name="search" size={18} /><input type="search" value={query} placeholder={t("Search saved places")}
        onChange={(event) => setQuery(event.target.value)} /></label> : null}
    {!count ? <div className="saved-place-empty"><Icon name="location" size={28} />
      <strong>{t("No saved places yet")}</strong>
      <p className="caption">{t("Save a place in Location zones, then return here to choose it.")}</p>
    </div> : null}
    {count > 0 && !matching.length ? <div className="saved-place-empty" role="status"><Icon name="search" size={24} />
      <strong>{t("No matching saved places")}</strong>
      <button type="button" className="text-button" onClick={() => setQuery("")}>{t("Clear search")}</button>
    </div> : null}
    {matching.length ? <div className="saved-place-list" role="group" aria-label={t("Saved places")}>
      {matching.map((zone) => <section className="saved-place-zone" key={zone.id} aria-label={zone.name}>
        <h4>{zone.name}</h4>
        {zone.locations.map((point) => {
          const current = selected?.zone_id === zone.id && selected?.location_id === point.id;
          return <button type="button" className="saved-place-choice" key={point.id}
            aria-label={`${zone.name}: ${point.label}`} aria-pressed={current} disabled={disabled}
            onClick={() => onChoose({ zone_id: zone.id, location_id: point.id })}>
            <span className="saved-place-name">{point.label}</span>
            {current ? <span className="saved-place-current"><Icon name="check" size={16} />{t("Current")}</span> :
              <span className="saved-place-check" aria-hidden="true" />}
          </button>;
        })}
      </section>)}
    </div> : null}
  </div>;
}

export function RecordingLocationEditor({ sourceID, onCancel, onSaved }: {
  sourceID: string; onCancel: () => void; onSaved: (selection: LocationSelection) => void;
}) {
  const [scope] = useState(profileScope);
  const path = `/v1/sources/${encodeURIComponent(sourceID)}/location?profile_id=${encodeURIComponent(scope.id)}`;
  const state = useResource<LocationSelection>(path);
  const zones = useResource<ZoneDirectory>(`/v1/location-zones?profile_id=${encodeURIComponent(scope.id)}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [needsReload, setNeedsReload] = useState(false);
  const [managing, setManaging] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const headingID = useId();
  const [lifetime] = useState(() => new AbortController());
  useEffect(() => () => lifetime.abort(), [lifetime]);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [managing]);
  function reload() { state.refresh(); zones.refresh(); setNeedsReload(false); setError(""); }
  async function save(location: SavedPlaceReference | null) {
    if (busy || !state.data || needsReload) return;
    if (location && state.data.manual && state.data.location && "zone_id" in state.data.location &&
        state.data.location.zone_id === location.zone_id && state.data.location.location_id === location.location_id) {
      onSaved(state.data); return;
    }
    setBusy(true); setError("");
    try {
      const result = await request<LocationSelection>(path, { method: "PUT", signal: lifetime.signal,
        body: JSON.stringify({ location, expected_version: state.data.version }) }, scope);
      if (!scope.signal.aborted && !lifetime.signal.aborted) onSaved(result);
    } catch (error) {
      if (!scope.signal.aborted && !lifetime.signal.aborted) {
        setError(errorMessage(error));
        setNeedsReload(error instanceof ApiError && (error.status === 409 || error.status === 404));
      }
    } finally { if (!scope.signal.aborted && !lifetime.signal.aborted) setBusy(false); }
  }
  const selected = state.data?.manual && state.data.location && "zone_id" in state.data.location ? state.data.location : undefined;
  const disabled = busy || !state.data || state.loading || zones.loading || Boolean(state.error || zones.error) || needsReload;
  const empty = zones.data && !zones.data.items.some((zone) => zone.locations.length);
  if (managing) return <div className="saved-place-manager">
    <LocationZones onBack={() => { setManaging(false); reload(); }} />
  </div>;
  return <div className="saved-place-picker" role="region" aria-labelledby={headingID} aria-busy={busy}
    onKeyDown={(event) => { if (event.key === "Escape" && !busy) { event.stopPropagation(); onCancel(); } }}>
    <div className="saved-place-picker-heading">
      <h4 id={headingID} ref={heading} tabIndex={-1}>{t("Choose a saved place")}</h4>
      <button type="button" className="icon-button" aria-label={t("Cancel")} disabled={busy} onClick={onCancel}><Icon name="close" size={18} /></button>
    </div>
    {!empty ? <p className="caption saved-place-guidance">{t("Choose a place below. Changes apply immediately.")}</p> : null}
    {state.error ? <ErrorState message={state.error} retry={reload} /> : null}
    {zones.error ? <ErrorState message={zones.error} retry={reload} /> : null}
    {(state.loading || zones.loading) && (!state.data || !zones.data) ? <Loading /> : null}
    {zones.data ? <SavedPlaceChoices zones={zones.data.items} selected={selected || undefined} disabled={disabled} onChoose={(place) => void save(place)} /> : null}
    {busy ? <p className="caption" role="status">{t("Saving…")}</p> : null}
    {error ? <ErrorState message={error} retry={reload} /> : null}
    <div className="saved-place-picker-footer">
      <button type="button" className={empty ? "button small" : "text-button"} disabled={busy} onClick={() => setManaging(true)}>
        <Icon name="plus" size={16} />{t("Manage saved places")}
      </button>
      {state.data?.manual ? <button type="button" className="text-button" disabled={disabled} onClick={() => void save(null)}>
        <Icon name={state.data.original_location ? "rewind" : "close"} size={16} />
        {state.data.original_location ? t("Restore captured location") : t("Remove location")}
      </button> : null}
    </div>
    {state.data?.original_location ? <p className="caption saved-place-preservation">{t("The original captured location is kept.")}</p> : null}
  </div>;
}
