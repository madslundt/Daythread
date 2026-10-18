import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ZoneEditor, type LocationZone } from "./LocationZones";
import { searchPlaces } from "./ZoneLocationPicker";

const zone: LocationZone = { id: "work", name: "Work", version: 2, locations: [
  { id: "office", label: "Main office", latitude: 55.6761, longitude: 12.5683, radius_m: 150 },
  { id: "other", label: "Second office", latitude: 56, longitude: 10, radius_m: 500 },
] };

describe("zone editing", () => {
  it("keeps separate place names and radii with optional coordinate controls", () => {
    const html = renderToStaticMarkup(<ZoneEditor zone={zone} onCancel={() => {}} onSaved={() => {}} />);
    expect(html).toContain('value="Main office"');
    expect(html).toContain('value="Second office"');
    expect(html).toContain('value="150"');
    expect(html).toContain('value="500"');
    expect(html).toContain('href="https://maps.apple.com/?ll=55.6761,12.5683"');
    expect(html).toContain('min="10"');
    expect(html).toContain('max="100000"');
    expect(html).toContain('Place or address');
    expect(html).toContain('Zone location map');
    expect(html).toContain('Advanced settings');
    expect(html).not.toContain('<details class="zone-coordinates" open');
  });

  it("requires coordinates before saving and never offers a NaN Maps link", () => {
    const draft = { ...zone, locations: [{ ...zone.locations[0], latitude: NaN, longitude: NaN }] };
    const html = renderToStaticMarkup(<ZoneEditor zone={draft} onCancel={() => {}} onSaved={() => {}} />);
    expect(html).not.toContain("NaN");
    expect(html).not.toContain("maps.apple.com");
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled/);
  });
});

describe("address results", () => {
  it("converts GeoJSON longitude/latitude to the saved zone coordinate order", () => {
    expect(searchPlaces({ features: [{ geometry: { type: "Point", coordinates: [12.5683, 55.6761] },
      properties: { name: "Office", street: "Main Street", housenumber: "12", city: "Copenhagen", country: "Denmark" } }] }))
      .toEqual([{ label: "Office", address: "Main Street 12, Copenhagen, Denmark", latitude: 55.6761, longitude: 12.5683 }]);
  });

  it("uses a street address when there is no place name and ignores invalid results", () => {
    expect(searchPlaces({ features: [
      { geometry: { type: "Point", coordinates: [0, 0] }, properties: { street: "Main Street", housenumber: "1" } },
      { geometry: { type: "Point", coordinates: [181, 55] }, properties: { name: "Invalid longitude" } },
      { geometry: { type: "Point", coordinates: [12, 91] }, properties: { name: "Invalid latitude" } },
      { geometry: { type: "Point", coordinates: [12, NaN] }, properties: { name: "Not finite" } },
      { geometry: { type: "LineString", coordinates: [12, 55] }, properties: { name: "Not a point" } },
      {},
    ] })).toEqual([{ label: "Main Street 1", address: "Main Street 1", latitude: 0, longitude: 0 }]);
    expect(searchPlaces({ features: [] })).toEqual([]);
  });
});
