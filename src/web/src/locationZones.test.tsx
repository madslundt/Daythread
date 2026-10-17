import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ZoneEditor, type LocationZone } from "./LocationZones";

const zone: LocationZone = { id: "work", name: "Work", version: 2, locations: [
  { id: "office", label: "Main office", latitude: 55.6761, longitude: 12.5683, radius_m: 150 },
  { id: "other", label: "Second office", latitude: 56, longitude: 10, radius_m: 500 },
] };

describe("zone editing", () => {
  it("keeps the separate location names, coordinates and radii visible", () => {
    const html = renderToStaticMarkup(<ZoneEditor zone={zone} onCancel={() => {}} onSaved={() => {}} />);
    expect(html).toContain('value="Main office"');
    expect(html).toContain('value="Second office"');
    expect(html).toContain('value="150"');
    expect(html).toContain('value="500"');
    expect(html).toContain('href="https://maps.apple.com/?ll=55.6761,12.5683"');
    expect(html).toContain('min="10"');
    expect(html).toContain('max="100000"');
  });

  it("requires coordinates before saving and never offers a NaN Maps link", () => {
    const draft = { ...zone, locations: [{ ...zone.locations[0], latitude: NaN, longitude: NaN }] };
    const html = renderToStaticMarkup(<ZoneEditor zone={draft} onCancel={() => {}} onSaved={() => {}} />);
    expect(html).not.toContain("NaN");
    expect(html).not.toContain("maps.apple.com");
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled/);
  });
});
