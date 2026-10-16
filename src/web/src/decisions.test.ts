import { describe, expect, it } from "vitest";
import { decisionAnchor, evidencePassage } from "./decisionModels";
import type { Source } from "./types";

const source: Source = {
  id: "source-id", source_version: "source-version", device_id: "import",
  started_at: "2026-10-16T09:00:00Z", ended_at: "2026-10-16T09:02:00Z",
  state: "done", text: "🎙️ Vi vælger A 🧑‍💻. Fordi det virker.",
};
describe("decision evidence anchors", () => {
  it("uses backend Unicode code points through emoji, accents and joiners", () => {
    const anchor = decisionAnchor(source, "Vi vælger A 🧑‍💻.");
    expect(anchor.start).toBe(3);
    expect(Array.from(source.text!).slice(anchor.start, anchor.end).join("")).toBe(anchor.quote);
    expect(anchor.source_version).toBe(source.source_version);
  });
  it("rejects approximate or invented quotations", () => {
    expect(() => decisionAnchor(source, "Vi vælger B")).toThrow("exactly");
  });
  it("anchors short excerpts inside long imported transcripts without copying the entire source", () => {
    const imported = { ...source, text: "🎙️".repeat(15000) + "We agreed to use A." };
    const anchor = decisionAnchor(imported, "We agreed to use A.");
    expect(anchor.start).toBe(30000);
    expect(() => decisionAnchor(imported, imported.text)).toThrow("at most 20,000");
  });
  it("requires disambiguation of repeated quotations", () => {
    expect(() => decisionAnchor({ ...source, text: "Yes. Yes." }, "Yes.")).toThrow("more than once");
  });
  it("refuses unversioned evidence and empty quotations", () => {
    expect(() => decisionAnchor({ ...source, source_version: undefined }, "Vi vælger A")).toThrow("current transcript");
    expect(() => decisionAnchor(source, " ")).toThrow("supporting quotation");
  });
});

describe("decision passage highlighting", () => {
  it("highlights exactly the cited codepoint passage with surrounding context", () => {
    const anchor = decisionAnchor(source, "Vi vælger A 🧑‍💻.");
    const passage = evidencePassage(source, anchor);
    expect(passage).toEqual({ before: "🎙️ ", quote: anchor.quote, after: " Fordi det virker." });
  });
  it("never highlights a corrected or mismatched passage", () => {
    const anchor = decisionAnchor(source, "Vi vælger A 🧑‍💻.");
    expect(evidencePassage({ ...source, source_version: "v2" }, anchor)).toBeNull();
    expect(evidencePassage(source, { ...anchor, start: 0 })).toBeNull();
    expect(evidencePassage(source, { ...anchor, source_id: "another-source" })).toBeNull();
  });
});
