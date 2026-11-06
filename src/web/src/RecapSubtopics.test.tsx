import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RecapTopic } from "./RecapTopic";
import { recapParts, recapTopics } from "./recapItems";

const day = "2026-11-04";
const text = "## Product review\nA shared project discussion.\n### Release\nBeta is planned.\n### Bookmarks\nLinks preserve state.";
const props = { sources: [], timezone: "UTC", onCorrect: () => {}, compact: false, inline: true, expanded: true };

describe("expandable recap subtopics", () => {
  it("gives each child a separate collapsed disclosure while keeping the parent introduction", () => {
    const topic = recapTopics(recapParts(text), day).topics[0];
    const html = renderToStaticMarkup(<RecapTopic {...props} {...topic} />);
    expect(html.match(/class="recap-subtopic"/g)).toHaveLength(2);
    expect(html).not.toContain(" open=");
    const controls = [...html.matchAll(/<summary role="button" aria-expanded="false" aria-controls="([^"]+)">/g)];
    expect(controls).toHaveLength(2);
    expect(new Set(controls.map((match) => match[1])).size).toBe(2);
    for (const control of controls) expect(html).toContain(`id="${control[1]}"`);
    expect(html).toContain("A shared project discussion.");
    expect(html).toContain("<h4>Release</h4>");
    expect(html).toContain("<h4>Bookmarks</h4>");
    expect(html).toContain('aria-label="Copy summary: Product review"');
  });

  it("uses translated child labels and prose without changing the copy context", () => {
    const parts = recapParts(text);
    const topic = recapTopics(parts, day).topics[0];
    const translation = "## Produktgennemgang\nEn fælles projektsamtale.\n### Udgivelse\nBeta er planlagt.\n### Bogmærker\nLinks bevarer tilstanden.";
    const display = recapTopics(parts, day, recapParts(text, translation)).topics[0];
    const html = renderToStaticMarkup(<RecapTopic {...props} {...topic}
      displayTitle={display.title} displayParts={display.parts} />);
    expect(html.match(/class="recap-subtopic"/g)).toHaveLength(2);
    expect(html).toContain("<h4>Udgivelse</h4>");
    expect(html).toContain("Beta er planlagt.");
    expect(html).not.toContain("Beta is planned.");
    expect(html).toContain('aria-label="Copy summary: Produktgennemgang"');
  });

  it("keeps a simple topic as prose without adding a child disclosure", () => {
    const topic = recapTopics(recapParts("## Bicycle\nCollect Monday."), day).topics[0];
    const html = renderToStaticMarkup(<RecapTopic {...props} {...topic} />);
    expect(html).not.toContain("recap-subtopic");
    expect(html).toContain("Collect Monday.");
  });
});
