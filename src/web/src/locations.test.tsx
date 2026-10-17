import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SourceList } from "./SourceList";
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
    expect(html).not.toContain("Conversation location");
    expect(html).not.toContain("maps.apple.com");
  });
});
