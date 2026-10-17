import { useEffect, useState } from "react";
import { t } from "./i18n";
import { errorMessage, newId, request } from "./api";
import { profileScope } from "./profiles";
import { useResource } from "./useResource";
import { ErrorState, Loading } from "./UI";

export type ZoneLocation = { id: string; label: string; latitude: number; longitude: number; radius_m: number };
export type LocationZone = { id: string; name: string; version: number; locations: ZoneLocation[] };
export type ZoneDirectory = { items: LocationZone[] };

export function ZoneFilter({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const zones = useResource<ZoneDirectory>(`/v1/location-zones?profile_id=${encodeURIComponent(profileScope().id)}`, 15_000);
  useEffect(() => {
    window.addEventListener("omi:zones-changed", zones.refresh);
    return () => window.removeEventListener("omi:zones-changed", zones.refresh);
  }, [zones.refresh]);
  return <div className="zone-filter">
    <label>{t("Location zone")}{" "}<select value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{t("All locations")}</option>
      {value && !zones.data?.items.some((zone) => zone.id === value) ? <option value={value}>{t("Zone unavailable")}</option> : null}
      {zones.data?.items.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}
    </select></label>{" "}<a href="/settings/locations">{t("Manage zones")}</a>
    {zones.error ? <ErrorState message={zones.error} retry={zones.refresh} /> : null}
    {value ? <p className="caption">{t("Filters recordings with GPS inside this zone. The day recap still covers the whole day.")}</p> : null}
  </div>;
}

export function LocationZones() {
  const resource = useResource<ZoneDirectory>(`/v1/location-zones?profile_id=${encodeURIComponent(profileScope().id)}`);
  const [editor, setEditor] = useState<LocationZone>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [scope] = useState(profileScope);
  function point(): ZoneLocation {
    const params = new URLSearchParams(window.location.search);
    const lat = params.has("latitude") ? Number(params.get("latitude")) : NaN;
    const lon = params.has("longitude") ? Number(params.get("longitude")) : NaN;
    return { id: newId(), label: t("Location"), radius_m: 200,
      latitude: Number.isFinite(lat) && Math.abs(lat) <= 90 ? lat : NaN,
      longitude: Number.isFinite(lon) && Math.abs(lon) <= 180 ? lon : NaN };
  }
  async function remove(zone: LocationZone) {
    if (!window.confirm(t("Delete this zone? Original recording locations are kept."))) return;
    setBusy(true); setError("");
    try {
      await request(`/v1/location-zones/${zone.id}?expected_version=${zone.version}`, { method: "DELETE" }, scope);
      if (!scope.signal.aborted) { resource.refresh(); window.dispatchEvent(new Event("omi:zones-changed")); }
    } catch (error) { if (!scope.signal.aborted) setError(errorMessage(error)); }
    finally { if (!scope.signal.aborted) setBusy(false); }
  }
  return <section className="location-zones">
    <h2>{t("Location zones")}</h2>
    <p>{t("Group places as Home, Work, Gym, or any name you choose. Each zone can contain several locations, each with its own radius.")}</p>
    <p className="caption">{t("Zones belong to this profile. Changes apply to past recordings without changing their original GPS observations. Overlapping zones can both match. Recordings without GPS stay unclassified.")}</p>
    {error ? <ErrorState message={error} retry={resource.refresh} /> : null}
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {resource.loading && !resource.data ? <Loading /> : null}
    {editor ? <ZoneEditor key={editor.id} zone={editor} onCancel={() => setEditor(undefined)} onSaved={() => {
      setEditor(undefined); resource.refresh(); window.dispatchEvent(new Event("omi:zones-changed"));
    }} /> : <>
      {resource.data?.items.map((zone) => <fieldset key={zone.id} disabled={busy}>
        <legend>{zone.name}</legend>
        <ul>{zone.locations.map((location) => <li key={location.id}>{location.label}{" · "}{location.latitude.toFixed(5)}, {location.longitude.toFixed(5)}{" · "}{location.radius_m}{" "}{t("m")}</li>)}</ul>
        <div className="button-row"><button className="button" onClick={() => setEditor(zone)}>{t("Edit zone")}</button>
          <button className="button" onClick={() => setEditor({ ...zone, locations: [...zone.locations, point()] })}>{t("Add location")}</button>
          <button className="text-button" onClick={() => void remove(zone)}>{t("Delete zone")}</button></div>
      </fieldset>)}
      <button className="button" disabled={busy || !resource.data} onClick={() => setEditor({ id: newId(), name: "", version: 0, locations: [point()] })}>{t("Create zone")}</button>
    </>}
  </section>;
}

export function ZoneEditor({ zone, onCancel, onSaved }: { zone: LocationZone; onCancel: () => void; onSaved: () => void }) {
  const [draft, setDraft] = useState(zone);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [scope] = useState(profileScope);
  const [lifetime] = useState(() => new AbortController());
  useEffect(() => () => lifetime.abort(), [lifetime]);
  function change(id: string, changes: Partial<ZoneLocation>) {
    setDraft((value) => ({ ...value, locations: value.locations.map((point) => point.id === id ? { ...point, ...changes } : point) }));
  }
  async function locate(id: string) {
    if (!navigator.geolocation) { setError(t("GPS is unavailable here. Enter coordinates or open a recording location.")); return; }
    setBusy(true); setError("");
    navigator.geolocation.getCurrentPosition((position) => {
      if (scope.signal.aborted || lifetime.signal.aborted) return;
      change(id, { latitude: position.coords.latitude, longitude: position.coords.longitude }); setBusy(false);
    }, () => {
      if (scope.signal.aborted || lifetime.signal.aborted) return;
      setError(t("GPS is unavailable here. Enter coordinates or open a recording location.")); setBusy(false);
    }, { maximumAge: 0, timeout: 15000, enableHighAccuracy: false });
  }
  async function save(event: React.SubmitEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await request(`/v1/location-zones/${draft.id}`, { method: "PUT", body: JSON.stringify({
        name: draft.name, locations: draft.locations, expected_version: draft.version,
      }) }, scope);
      if (!scope.signal.aborted && !lifetime.signal.aborted) onSaved();
    } catch (error) { if (!scope.signal.aborted && !lifetime.signal.aborted) setError(errorMessage(error)); }
    finally { if (!scope.signal.aborted && !lifetime.signal.aborted) setBusy(false); }
  }
  const canSave = draft.name.trim().length > 0 && draft.name.length <= 100 && draft.locations.length > 0 && draft.locations.every((point) =>
    point.label.trim().length > 0 && point.label.length <= 100 && Number.isFinite(point.latitude) && Math.abs(point.latitude) <= 90 &&
    Number.isFinite(point.longitude) && Math.abs(point.longitude) <= 180 && Number.isFinite(point.radius_m) && point.radius_m >= 10 && point.radius_m <= 100000);
  return <form onSubmit={save}>
    <fieldset disabled={busy}>
      <legend>{t("Edit zone")}</legend>
      <label className="setting-field">{t("Zone name")}<input required maxLength={100} value={draft.name} placeholder={t("Home, Work, Gym…")} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
      {draft.locations.map((point) => <fieldset key={point.id}>
        <legend>{t("Zone location")}</legend>
        <div className="settings-grid">
          <label className="setting-field">{t("Location name")}<input required maxLength={100} value={point.label} onChange={(event) => change(point.id, { label: event.target.value })} /></label>
          <label className="setting-field">{t("Latitude")}<input type="number" required min={-90} max={90} step="any" value={Number.isFinite(point.latitude) ? point.latitude : ""} onChange={(event) => change(point.id, { latitude: event.target.valueAsNumber })} /></label>
          <label className="setting-field">{t("Longitude")}<input type="number" required min={-180} max={180} step="any" value={Number.isFinite(point.longitude) ? point.longitude : ""} onChange={(event) => change(point.id, { longitude: event.target.valueAsNumber })} /></label>
          <label className="setting-field">{t("Radius in metres")}<input type="number" required min={10} max={100000} step="any" value={point.radius_m} onChange={(event) => change(point.id, { radius_m: event.target.valueAsNumber })} /></label>
        </div>
        <div className="button-row"><button type="button" className="button" onClick={() => void locate(point.id)}>{t("Use current location")}</button>
          {Number.isFinite(point.latitude) && Number.isFinite(point.longitude) ? <a href={`https://maps.apple.com/?ll=${point.latitude},${point.longitude}`} target="_blank" rel="noreferrer">{t("Open in Maps")}</a> : null}
          <button type="button" className="text-button" disabled={draft.locations.length === 1} onClick={() => setDraft({ ...draft, locations: draft.locations.filter((p) => p.id !== point.id) })}>{t("Remove location")}</button></div>
      </fieldset>)}
      <button type="button" className="button" disabled={draft.locations.length >= 30} onClick={() => setDraft({ ...draft, locations: [...draft.locations, { id: newId(), label: t("Location"), latitude: NaN, longitude: NaN, radius_m: 200 }] })}>{t("Add location")}</button>
      <p className="caption">{t("Matching uses the recorded GPS point and is approximate. Check its accuracy when choosing a small radius.")}</p>
      {error ? <p role="alert">{error}</p> : null}
      <div className="button-row"><button type="submit" className="button accent" disabled={!canSave}>{busy ? t("Saving…") : t("Save zone")}</button>
        <button type="button" className="button" onClick={onCancel}>{t("Cancel")}</button></div>
    </fieldset>
  </form>;
}
