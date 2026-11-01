import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SourceList } from "./SourceList";
import type { Source } from "./types";

const source: Source = {
  id: "fb58c342-6eba-52aa-a447-4807587c8606", device_id: "pendant",
  started_at: "2026-10-16T12:00:00Z", ended_at: "2026-10-16T12:00:01Z", state: "done",
  text: "Review Friday.", source_kind: "manual_upload", source_id: "omi-ring:pendant:99:10",
};
const note = "Pendant storage uses the device clock with one-second precision. Device copies were retained.";
const render = (value: Source) => renderToStaticMarkup(<SourceList timezone="UTC" expanded sources={[value]} />);

describe("pendant storage provenance", () => {
  it("discloses device-clock precision and retained copies from backend-derived metadata", () => {
    const html = render({ ...source, source_metadata: {
      provenance_kind: "omi_ring", timestamp_precision_seconds: 1, device_copy_retained: true,
    } });
    expect(html).toContain(note);
    expect(html).not.toContain("Capture time was supplied when importing");
    expect(html).not.toContain(source.source_id);
  });
  it("does not recognize a filename prefix or incomplete flags as provenance", () => {
    for (const value of [source, { ...source, source_metadata: { provenance_kind: "omi_ring" } },
      { ...source, source_metadata: { provenance_kind: "omi_ring", timestamp_precision_seconds: 1, device_copy_retained: false } },
      { ...source, source_kind: "omi_api", source_metadata: { provenance_kind: "omi_ring", timestamp_precision_seconds: 1, device_copy_retained: true } }]) {
      expect(render(value)).not.toContain(note);
    }
    expect(render(source)).toContain("Capture time was supplied when importing");
  });
});
