import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SourceList } from "./SourceList";
import { RecordingLocationEditor, SavedPlaceChoices } from "./RecordingLocation";
import type { Source } from "./types";

const recording: Source = {
  id: "recording", device_id: "pendant", started_at: "2026-10-15T12:00:00Z",
  ended_at: "2026-10-15T12:00:30Z", state: "done", text: "Pick up the bike.",
};

describe("conversation location provenance", () => {
  it("shows observation coordinates, accuracy and a deliberate Maps link without loading a remote map", () => {
    const source: Source = { ...recording, source_metadata: { location: {
      latitude: 0, longitude: 0, horizontal_accuracy: 35, captured_at: "2026-10-15T11:59:40Z",
    } } };
    const html = renderToStaticMarkup(<SourceList sources={[source]} timezone="Europe/Copenhagen" expanded />);
    expect(html).toContain("0.00000, 0.00000");
    expect(html).toContain("±35");
    expect(html).toContain("https://maps.apple.com/?ll=0,0");
    expect(html).toContain("does not identify places mentioned");
    expect(html).not.toContain("<iframe");
  });

  it("does not invent a location for an older recording", () => {
    const html = renderToStaticMarkup(<SourceList sources={[recording]} timezone="Europe/Copenhagen" expanded />);
    expect(html).toContain("Add location");
    expect(html).toContain("No location");
    expect(html).not.toContain("maps.apple.com");
  });

  it("shows the manual location and edit action while keeping captured GPS separate", () => {
    const source: Source = { ...recording,
      manual_location: { label: "Office", latitude: 55, longitude: 12 },
      source_metadata: { location: { latitude: 1, longitude: 2, horizontal_accuracy: 35, captured_at: "2026-10-15T12:00:00Z" } },
    };
    const html = renderToStaticMarkup(<SourceList sources={[source]} timezone="Europe/Copenhagen" expanded compact />);
    expect(html).toContain("Office");
    expect(html).not.toContain("55.00000, 12.00000");
    expect(html).toContain("Edit location");
    expect(html).toContain("Manually set location");
    expect(html).not.toContain("1.00000, 2.00000");
    expect(html).not.toContain("±35");
  });

  it("uses saved-place choices with a selected indicator, without name or coordinate entry", () => {
    const zones = [{ id: "work", name: "Work", version: 1, locations: [
      { id: "office", label: "Main office", latitude: 55, longitude: 12, radius_m: 200 },
    ] }];
    const html = renderToStaticMarkup(<SavedPlaceChoices zones={zones}
      selected={{ zone_id: "work", location_id: "office" }} disabled={false} onChoose={() => {}} />);
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain("Work");
    expect(html).toContain("Main office");
    expect(html).toContain('aria-label="Work: Main office"');
    expect(html).toContain("Current");
    expect(html).toContain('<h4>Work</h4>');
    expect(html).not.toContain("<input");
    const editor = renderToStaticMarkup(<RecordingLocationEditor sourceID="recording" onCancel={() => {}} onSaved={() => {}} />);
    expect(editor).toContain("Manage saved places");
    expect(editor).not.toContain("Latitude");
    expect(editor).not.toContain("Longitude");
    expect(editor).not.toContain("Location name");
    expect(editor).not.toContain("Save location");
  });

  it("guides users to save a place and return when none are available", () => {
    const html = renderToStaticMarkup(<SavedPlaceChoices zones={[]} disabled={false} onChoose={() => {}} />);
    expect(html).toContain("No saved places yet");
    expect(html).toContain("Save a place in Location zones, then return here to choose it.");
    expect(html).not.toContain("<button");
  });

  it("gives saved places a readable name and zone without foregrounding coordinates", () => {
    const source: Source = { ...recording, manual_location: {
      label: "Main office", latitude: 55, longitude: 12, zone_id: "work", location_id: "office", zone_name: "Work",
    } };
    const html = renderToStaticMarkup(<SourceList sources={[source]} timezone="Europe/Copenhagen" expanded />);
    expect(html).toContain("<strong>Main office</strong>");
    expect(html).toContain("Work · Saved place");
    expect(html).not.toContain("55.00000");
    expect(html).toContain("https://maps.apple.com/?ll=55,12");
  });
});
