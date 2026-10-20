import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RecapContent, RecapTopicRow } from "./RecapContent";
import { recapBrowserTopics, recapParts, recapTopics, topicFocus } from "./recapItems";
import { recapTranslationDisplay } from "./recapTranslationDisplay";
import type { SummaryTranslationStructure } from "./transcriptTranslationRequests";
import type { Source } from "./types";

function structure(original: string, translated: string): SummaryTranslationStructure {
  const replacements = translated.split("\n");
  let offset = 0;
  const blocks: SummaryTranslationStructure["blocks"] = [];
  for (const [index, line] of original.split("\n").entries()) {
    if (line.trim()) blocks.push({ index: blocks.length, kind: line.startsWith("#") ? "heading" : line.startsWith("-") ? "item" : "text",
      start_offset: offset, end_offset: offset + Array.from(line).length, original_text: line, text: replacements[index] });
    offset += Array.from(line).length + 1;
  }
  return { version: 1, summary_version: "canonical-content-sha256", blocks };
}
const day = "2026-10-16";
const source: Source = { id: "11111111-1111-4111-8111-111111111111", device_id: "test", started_at: `${day}T10:00:00Z`,
  ended_at: `${day}T10:01:00Z`, state: "done", text: "Cocoa trial. Garden tools.", sections: [
    { title: "Cocoa", start_offset: 0, end_offset: 13 }, { title: "Garden", start_offset: 13, end_offset: 25 },
  ] };
const original = `## Cocoa ☕️\nA cocoa trial [${source.id}].\n\n## Follow-ups\n- Buy cocoa.`;
const translated = `## Kakao ☕️\nEt kakaoforsøg [${source.id}].\n\n## Opfølgning\n- Køb kakao.`;
const props = { day, version: "original-output", sources: [source], timezone: "UTC", onCorrect: () => {} };

describe("canonical recap translation display", () => {
  it("groups repeated headings by their canonical subject even when translations differ", () => {
    const before = "## Work\nMorning review.\n## Garden\nGarden plans.\n## WORK\nAfternoon decision.";
    const after = "## Arbejde\nMorgenmøde.\n## Have\nHaveplaner.\n## Job\nEftermiddagens beslutning.";
    const display = recapTopics(recapParts(before), day, recapParts(before, after));
    expect(display.topics.map((topic) => topic.title)).toEqual(["Arbejde", "Have"]);
    expect(display.topics[0].parts.map((part) => part.text)).toEqual(["Morgenmøde.", "Eftermiddagens beslutning."]);
  });
  it("projects translated physical lines at Unicode scalar offsets without changing topic identities or action grouping", () => {
    const mapped = structure(original, translated);
    expect(recapTranslationDisplay(original, translated, mapped)).toBe(translated);
    const parts = recapParts(original);
    const display = recapTopics(parts, day, recapParts(original, translated));
    expect(display.topics.map((topic) => topic.title)).toEqual(["Kakao ☕️"]);
    expect(display.followups).toEqual([{ kind: "item", text: "Køb kakao.", action: true }]);
    const html = renderToStaticMarkup(<RecapContent {...props} text={original} translatedText={translated} translationStructure={mapped} />);
    expect(html).toContain("Kakao ☕️"); expect(html).toContain("Et kakaoforsøg.");
    expect(html).toContain('data-topic-id="recap:0:Cocoa ☕️"');
    expect(html).toContain("Follow-ups mentioned in the recap");
    expect(html).not.toContain("Opfølgning");
    expect(html).not.toContain("Update this translation");
  });

  it("keeps translated title and body display-only in an expanded row while exact source focus remains canonical", () => {
    const canonical = recapTopics(recapParts(original), day).topics[0];
    const display = recapTopics(recapParts(original), day, recapParts(original, translated)).topics[0];
    const topic = { ...recapBrowserTopics([canonical], [source])[0], displayTitle: display.title, displayParts: display.parts };
    expect(topicFocus(topic, [source])).toEqual({ title: "Cocoa ☕️", passages: [{ source_id: source.id, start: 0, end: 13 }] });
    const html = renderToStaticMarkup(<RecapTopicRow {...props} topic={topic} expanded onToggle={() => {}} />);
    expect(html).toContain("Kakao ☕️"); expect(html).not.toContain("Cocoa ☕️</");
    expect(html).toContain('aria-label="Copy summary: Kakao ☕️"');
    expect(html).toContain("Show original passages");
    expect(html).not.toContain("Cocoa trial."); expect(html).not.toContain("Garden tools.");
    expect(html).not.toContain("<audio");
  });

  it("uses original diary headings and action flags even when their translated labels resemble another group", () => {
    const before = "## Diary Recap: 2026-10-16\nA short introduction.\n## Cocoa\nA trial.\n## Follow-ups\n- Buy cocoa.";
    const after = "## Dagsoversigt: 2026-10-16\nEn kort indledning.\n## Follow-ups\nEt forsøg.\n## Cocoa\n- Køb kakao.";
    const result = recapTopics(recapParts(before), day, recapParts(before, after));
    expect(result.introduction).toEqual([{ kind: "text", text: "En kort indledning." }]);
    expect(result.topics).toEqual([{ title: "Follow-ups", parts: [{ kind: "text", text: "Et forsøg." }] }]);
    expect(result.followups).toEqual([{ kind: "item", text: "Køb kakao.", action: true }]);
  });

  it.each(["absent", "stale", "dropped", "reordered", "citation", "extra", "injected", "flattened"])("falls back to the canonical list with guidance for %s mapping", (failure) => {
    let mapped: SummaryTranslationStructure | undefined = structure(original, translated);
    let target = translated;
    if (failure === "absent") mapped = undefined;
    if (failure === "stale") mapped!.blocks[0].original_text = "## Another recap";
    if (failure === "dropped") mapped!.blocks.splice(1, 1);
    if (failure === "reordered") mapped!.blocks.reverse();
    if (failure === "citation") { target = translated.replace(source.id, "22222222-2222-4222-8222-222222222222"); mapped = structure(original, target); }
    if (failure === "extra") target += "\n## Injected topic";
    if (failure === "injected") { target = translated.replace("Et kakaoforsøg", "- **Injected topic** — Et kakaoforsøg"); mapped = structure(original, target); }
    if (failure === "flattened") mapped = { ...mapped!, blocks: [{ index: 0, kind: "text", start_offset: 0,
      end_offset: Array.from(original).length, original_text: original, text: "A flattened translation." }] };
    expect(recapTranslationDisplay(original, target, mapped)).toBeUndefined();
    const html = renderToStaticMarkup(<RecapContent {...props} text={original} translatedText={target} translationStructure={mapped} />);
    expect(html).toContain("Update this translation to keep the recap’s topics and layout.");
    expect(html).toContain("A cocoa trial."); expect(html).not.toContain("Et kakaoforsøg.");
    expect(html).toContain('data-topic-id="recap:0:Cocoa ☕️"');
  });
});
