import { useEffect, useId, useRef, useState } from "react";
import type { Circle, Map as LeafletMap, Marker } from "leaflet";
import "leaflet/dist/leaflet.css";
import { t } from "./i18n";
import { Icon } from "./Icon";
import type { ZoneLocation } from "./LocationZones";

type Place = { label: string; address: string; latitude: number; longitude: number };
type PhotonFeature = {
  geometry?: { type?: string; coordinates?: number[] };
  properties?: Record<string, unknown>;
};

export function searchPlaces(data: { features?: PhotonFeature[] }): Place[] {
  return (data.features ?? []).flatMap((feature) => {
    const [longitude, latitude] = feature.geometry?.coordinates ?? [];
    if (feature.geometry?.type !== "Point" || !validCoordinates(latitude, longitude)) return [];
    const props = feature.properties ?? {};
    const text = (key: string) => typeof props[key] === "string" ? props[key] : "";
    const street = [text("street"), text("housenumber")].filter(Boolean).join(" ");
    const address = [...new Set([street, text("postcode"), text("city"), text("state"), text("country")].filter(Boolean))].join(", ");
    const label = text("name") || street || text("city");
    return label ? [{ label, address, latitude, longitude }] : [];
  });
}

export function validCoordinates(latitude: number, longitude: number) {
  return Number.isFinite(latitude) && Math.abs(latitude) <= 90 && Number.isFinite(longitude) && Math.abs(longitude) <= 180;
}

export function ZoneLocationPicker({ point, disabled, onChange, onLocate }: {
  point: ZoneLocation; disabled: boolean; onChange: (changes: Partial<ZoneLocation>) => void; onLocate: () => void;
}) {
  const id = useId();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Place[]>([]);
  const [searching, setSearching] = useState(false);
  const [message, setMessage] = useState("");
  const [mapError, setMapError] = useState("");
  const container = useRef<HTMLDivElement>(null);
  const layers = useRef<{ map: LeafletMap; marker: Marker; circle: Circle }>(null);
  const search = useRef<AbortController>(null);
  const latest = useRef({ point, disabled, onChange });
  useEffect(() => { latest.current = { point, disabled, onChange }; });
  useEffect(() => () => search.current?.abort(), []);

  useEffect(() => {
    let active = true;
    let dispose: (() => void) | undefined;
    void import("leaflet").then((L) => {
      if (!active || !container.current) return;
      const initial = latest.current.point;
      const selected = validCoordinates(initial.latitude, initial.longitude);
      const center: [number, number] = selected ? [initial.latitude, initial.longitude] : [25, 0];
      const map = L.map(container.current, { scrollWheelZoom: false, worldCopyJump: true }).setView(center, selected ? 15 : 2);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }).on("tileerror", () => { if (active) setMapError(t("Map tiles could not load. You can still search or enter coordinates.")); }).addTo(map);
      const marker = L.marker(center, {
        draggable: !latest.current.disabled, title: t("Zone location"), alt: t("Zone location"),
        icon: L.divIcon({ className: "zone-map-pin", iconSize: [28, 36], iconAnchor: [14, 36] }),
      });
      const circle = L.circle(center, { radius: Number.isFinite(initial.radius_m) && initial.radius_m >= 10 && initial.radius_m <= 100000 ? initial.radius_m : 200, color: "#476c56", fillOpacity: 0.16, weight: 2, interactive: false });
      if (selected) { marker.addTo(map); circle.addTo(map); map.fitBounds(circle.getBounds(), { padding: [35, 35], maxZoom: 17 }); }
      function choose(latitude: number, longitude: number) {
        if (latest.current.disabled) return;
        latest.current.onChange({ latitude: Math.max(-90, Math.min(90, latitude)), longitude: ((longitude + 180) % 360 + 360) % 360 - 180 });
      }
      map.on("click", (event) => choose(event.latlng.lat, event.latlng.lng));
      marker.on("dragend", () => { const position = marker.getLatLng(); choose(position.lat, position.lng); });
      layers.current = { map, marker, circle };
      const observer = new ResizeObserver(() => map.invalidateSize());
      observer.observe(container.current);
      dispose = () => { observer.disconnect(); map.remove(); layers.current = null; };
    }).catch(() => { if (active) setMapError(t("Map tiles could not load. You can still search or enter coordinates.")); });
    return () => { active = false; dispose?.(); };
  }, []);

  useEffect(() => {
    const current = layers.current;
    if (!current) return;
    if (disabled) current.marker.dragging?.disable(); else current.marker.dragging?.enable();
    if (!validCoordinates(point.latitude, point.longitude)) {
      current.marker.remove(); current.circle.remove(); return;
    }
    const center: [number, number] = [point.latitude, point.longitude];
    current.marker.setLatLng(center).addTo(current.map);
    current.circle.setLatLng(center).addTo(current.map);
    if (Number.isFinite(point.radius_m) && point.radius_m >= 10 && point.radius_m <= 100000) {
      current.circle.setRadius(point.radius_m);
      current.map.fitBounds(current.circle.getBounds(), { padding: [35, 35], maxZoom: 17 });
    }
  }, [point.latitude, point.longitude, point.radius_m, disabled]);

  async function find() {
    if (!query.trim() || disabled) return;
    search.current?.abort();
    const operation = new AbortController(); search.current = operation;
    setSearching(true); setResults([]); setMessage("");
    try {
      const url = new URL(import.meta.env.VITE_ZONE_GEOCODER_URL || "https://photon.komoot.io/api/");
      url.searchParams.set("q", query.trim()); url.searchParams.set("limit", "5");
      const response = await fetch(url, { signal: AbortSignal.any([operation.signal, AbortSignal.timeout(15000)]), credentials: "omit" });
      if (!response.ok) throw new Error();
      const places = searchPlaces(await response.json());
      if (operation.signal.aborted) return;
      setResults(places);
      if (!places.length) setMessage(t("No places found. Try a more specific address."));
    } catch {
      if (!operation.signal.aborted) setMessage(t("Place search failed. Try again."));
    } finally { if (!operation.signal.aborted) setSearching(false); }
  }

  return <div className="zone-location-picker">
    <div className="zone-address-search">
      <div className="setting-field"><label className="zone-search-label" htmlFor={id}>{t("Place or address")}</label><div className="zone-search-input"><Icon name="search" size={18} /><input id={id} type="search" value={query} placeholder={t("Search for a place or address")} disabled={disabled} onChange={(event) => {
        search.current?.abort(); setSearching(false); setResults([]); setMessage(""); setQuery(event.target.value);
      }} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void find(); } }} /></div></div>
      <button type="button" className="button" disabled={disabled || searching || !query.trim()} onClick={() => void find()}>{searching ? t("Searching…") : t("Search places")}</button>
    </div>
    <div className="zone-location-tools"><button type="button" className="text-button" disabled={disabled} onClick={onLocate}><Icon name="location" size={15} />{t("Use current location")}</button>
      <span className={`zone-location-status${validCoordinates(point.latitude, point.longitude) ? " is-selected" : ""}`} role="status">{validCoordinates(point.latitude, point.longitude) ? <><Icon name="check" size={14} />{t("Location selected")}</> : t("Choose a location to continue.")}</span>
    </div>
    {message ? <p role="status" className="caption">{message}</p> : null}
    {results.length ? <ul className="zone-search-results" aria-label={t("Search places")}>{results.map((place, index) => <li key={index}>
      <button type="button" disabled={disabled} onClick={() => {
        onChange({ latitude: place.latitude, longitude: place.longitude,
          ...(!point.label.trim() || point.label === t("Location") ? { label: place.label.slice(0, 100) } : {}) });
        setResults([]); setQuery([place.label, place.address].filter(Boolean).join(", "));
      }}><strong>{place.label}</strong><span>{place.address}</span></button>
    </li>)}</ul> : null}
    <div ref={container} className="zone-map" role="region" aria-label={t("Zone location map")} aria-describedby={`${id}-hint`} />
    <p id={`${id}-hint`} className="caption zone-map-hint">{t("Search for an address, click the map, or drag the pin to choose the zone center.")}</p>
    {mapError ? <p className="caption" role="status">{mapError}</p> : null}
    <p className="caption zone-map-provider">{t("Address searches use Photon; maps use OpenStreetMap.")} {" "}<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">{t("Attribution")}</a></p>
  </div>;
}
