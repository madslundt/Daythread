import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RecapContent } from "./RecapContent";
import { itemSources, recapParts } from "./recapItems";
import type { Source } from "./types";

const sources: Source[] = [
  { id: "11111111-1111-4111-8111-111111111111", device_id: "test", started_at: "2026-10-16T05:15:00Z", ended_at: "2026-10-16T05:17:00Z", state: "done", text: "Gym discussion" },
  { id: "22222222-2222-4222-8222-222222222222", device_id: "test", started_at: "2026-10-16T07:30:00Z", ended_at: "2026-10-16T07:32:00Z", state: "done", text: "Work discussion" },
];

describe("day recap interactions", () => {
  it("turns the sample's inline follow-ups into individual optional tasks", () => {
    const parts = recapParts("- **Gym · 07:15** — Strength session.\n- **Work · 09:30** — Test the prototype.\n\n**To follow up:** share the prototype, call the bike shop, bring the photo album, and confirm game night. Completion is not recorded.");
    expect(parts.filter((part) => part.kind === "item" && part.action).map((part) => part.text)).toEqual([
      "share the prototype", "call the bike shop", "bring the photo album", "confirm game night.",
    ]);
    expect(parts.filter((part) => part.kind === "item" && !part.action)).toHaveLength(2);
  });

  it("supports heading lists, numbered lists, task syntax and continuation lines", () => {
    const parts = recapParts("## Action items\n1. Share notes\n   before Thursday.\n- [x] Call Sam\n\n## Highlights\n+ A useful conversation\n\n**Next steps**\n- Book a service");
    expect(parts.filter((part) => part.kind === "item")).toEqual([
      { kind: "item", text: "Share notes before Thursday.", action: true },
      { kind: "item", text: "Call Sam", action: true },
      { kind: "item", text: "A useful conversation", action: false },
      { kind: "item", text: "Book a service", action: true },
    ]);
  });

  it("keeps commas inside a task and ordinary narrative intact", () => {
    expect(recapParts("To follow up: email Alice, Bob and Charlie, and confirm the time.")).toEqual([
      { kind: "heading", text: "To follow up" },
      { kind: "item", text: "email Alice, Bob and Charlie", action: true },
      { kind: "item", text: "confirm the time.", action: true },
    ]);
    expect(recapParts("No follow-ups were recorded.")).toEqual([{ kind: "text", text: "No follow-ups were recorded." }]);
  });

  it("uses explicit citations before subject matching", () => {
    expect(itemSources(`Gym at 07:15 [${sources[1].id.toUpperCase()}]`, sources)).toEqual({ sources: [sources[1]], basis: "citation" });
    expect(itemSources("[33333333-3333-4333-8333-333333333333]", sources)).toEqual({ sources: [], basis: "citation" });
  });

  it("opens only recordings mentioning the subject, including entries without timestamps", () => {
    expect(itemSources("**Gym** — Strength session with Sam.", sources).sources).toEqual([sources[0]]);
    expect(itemSources("**Work · 09:30** — Review the prototype.", sources).sources).toEqual([sources[1]]);
    expect(itemSources("**Gym · 09:30** — Strength session.", sources).sources).toEqual([sources[0]]);
  });

  it("never falls back to unrelated recordings, even at a matching time", () => {
    expect(itemSources("**Dentist · 07:15** — Arrange a dental appointment.", sources).sources).toEqual([]);
    expect(itemSources("General reflection", sources).sources).toEqual([]);
    expect(itemSources("**Gym**", [{ ...sources[1], text: "A work discussion about gymnasium renovation." }]).sources).toEqual([]);
  });

  it("finds recordings for descriptive subjects and follow-ups using distinctive words", () => {
    const errands = { ...sources[0], text: "The bike needs a service. I will call the repair shop." };
    expect(itemSources("**Around town** — Book a bike service.", [errands, sources[1]]).sources).toEqual([errands]);
    expect(itemSources("call the bike shop", [errands, sources[1]]).sources).toEqual([errands]);
    expect(itemSources("Review the plans for next week.", [{ ...sources[0], text: "We made plans for next week about something else." }]).sources).toEqual([]);
  });

  it("renders accessible entry controls, optional checks, and escaped model text", () => {
    const html = renderToStaticMarkup(<RecapContent
      text={'- **Gym · 07:15** — A workout.\n\n## Follow-ups\n- [x] <script>alert("unsafe")</script>'}
      day="2026-10-16" version="test" sources={sources} timezone="UTC" onChat={() => {}} onCorrect={() => {}}
    />);
    expect(html.match(/aria-expanded="false"/g)).toHaveLength(2);
    expect(html.match(/type="checkbox"/g)).toHaveLength(1);
    expect(html).not.toContain("checked=\"\"");
    expect(html).toContain("Optional checkmarks");
    expect(html).toContain("saved in this browser");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });
});
