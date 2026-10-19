import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RecapContent, RecapTopicRow } from "./RecapContent";
import { RecapEvidence } from "./RecapEvidence";
import { RecapTopic } from "./RecapTopic";
import { currentRecapPassage, itemSources, recapBrowserTopics, recapGroups, recapGroupSummary, recapParts, recapReadingText, recapSourceSections, recapTopicText, recapTopics, topicFocus } from "./recapItems";
import type { Source } from "./types";

const sources: Source[] = [
  { id: "11111111-1111-4111-8111-111111111111", device_id: "test", started_at: "2026-10-16T05:15:00Z", ended_at: "2026-10-16T05:17:00Z", state: "done", text: "Gym discussion" },
  { id: "22222222-2222-4222-8222-222222222222", device_id: "test", started_at: "2026-10-16T07:30:00Z", ended_at: "2026-10-16T07:32:00Z", state: "done", text: "Work discussion" },
];

describe("day recap interactions", () => {
  it("offers one searchable topic list without duplicate views or separate task state", () => {
    const html = renderToStaticMarkup(<RecapContent text={"## Cocoa\nA test with dark chocolate.\n## Follow-ups\n- Buy cocoa"}
      day="2026-10-16" version="test" sources={sources} timezone="UTC" onChat={() => {}} onCorrect={() => {}} />);
    expect(html).toContain('aria-label="Find a topic in this day"');
    expect(html).toContain('class="recap-topic-list"');
    expect(html).toContain('data-topic-id="recap:0:Cocoa"');
    expect(html).not.toContain("Day overview");
    expect(html).not.toContain("Explore topics");
    expect(html).not.toContain("Explore topic:");
    expect(html).not.toContain('type="checkbox"');
    expect(html).not.toContain("saved in this browser");
  });
  it("groups Markdown and bold section headings without changing saved point keys", () => {
    const text = `## Diary Recap: 2026-10-16\n\n**Highlights**\n- **Gym** — A workout [${sources[0].id}].\n\n## Follow-ups\n- Call Sam`;
    const parts = recapParts(text);
    const groups = recapGroups(parts, "2026-10-16");
    expect(groups.map((group) => group.title)).toEqual(["**Highlights**", "Follow-ups"]);
    expect(groups[0].parts[0]).toBe(parts[2]);
    expect(itemSources(groups[0].parts[0].text, sources).sources).toEqual([sources[0]]);
    expect(recapReadingText(groups[0].parts[0].text)).toBe("**Gym** — A workout.");
    expect(groups[1].parts).toEqual([{ kind: "item", text: "Call Sam", action: true }]);
  });

  it.each([false, true])("omits empty headings without losing topic points (compact=%s)", (compact) => {
    const text = "# Dagbogsoversigt 2026-10-16\n## Mælkeskummer\n- Kakao med mørk chokolade.\n## Omi\n- Optagelser og rewind.\n## Tesla Roadster\n- Samtale om bilen.\n## Åbne spørgsmål";
    const groups = recapGroups(recapParts(text), "2026-10-16");
    expect(groups.map((group) => group.title)).toEqual(["Mælkeskummer", "Omi", "Tesla Roadster"]);
    expect(groups.every((group) => group.parts.length > 0)).toBe(true);
    const html = renderToStaticMarkup(<RecapContent text={text} day="2026-10-16" version="test" sources={[]} timezone="UTC" onChat={() => {}} onCorrect={() => {}} compact={compact} />);
    expect(html).not.toContain("0 points");
    expect(html).not.toContain("Dagbogsoversigt");
    expect(html).toContain("Kakao med mørk chokolade");
  });

  it("preserves introductions, nonredundant titles, and ordinary bracketed text", () => {
    expect(recapGroups(recapParts("A short introduction.\n## Highlights\n- A point"), "2026-10-16")[0]).toEqual({ parts: [{ kind: "text", text: "A short introduction." }] });
    expect(recapGroups(recapParts("## A memorable day\n- A point"), "2026-10-16")[0].title).toBe("A memorable day");
    expect(recapReadingText("Bring [the original] notes.")).toBe("Bring [the original] notes.");
  });

  it("keeps legacy summaries and follow-ups readable in the same collapsed list", () => {
    const html = renderToStaticMarkup(<RecapContent
      text={`**Highlights**\n- **Gym** — A workout [${sources[0].id}].\n## Follow-ups\n- Call Sam`}
      day="2026-10-16" version="test" sources={sources} timezone="UTC" onChat={() => {}} onCorrect={() => {}} compact
    />);
    expect(html).toContain('data-topic-id="recap:0:Gym"');
    expect(html.match(/class="recap-topic-option" aria-expanded="false"/g)).toHaveLength(2);
    expect(html).toContain("Follow-ups mentioned in the recap");
    expect(html).toContain("A workout.");
    expect(html).not.toContain(sources[0].id);
  });

  it("turns the sample's inline follow-ups into individual optional tasks", () => {
    const parts = recapParts("- **Gym · 07:15** — Strength session.\n- **Work · 09:30** — Test the prototype.\n\n**To follow up:** share the prototype, call the bike shop, bring the photo album, and confirm game night. Completion is not recorded.");
    expect(parts.filter((part) => part.kind === "item" && part.action).map((part) => part.text)).toEqual([
      "share the prototype", "call the bike shop", "bring the photo album", "confirm game night.",
    ]);
    expect(parts.filter((part) => part.kind === "item" && !part.action)).toHaveLength(2);
  });

  it("shows one copyable topic summary without nested point controls", () => {
    const text = `## Cocoa\nA test with dark chocolate.\n- Milk frother and cocoa [${sources[0].id}].`;
    expect(recapGroupSummary(recapParts(text).slice(1))).toBe("A test with dark chocolate.");
    expect(recapGroupSummary(recapParts(`- Cocoa [${sources[0].id}].`))).toBe("Cocoa.");
    const html = renderToStaticMarkup(<RecapTopic title="Cocoa" parts={recapParts(text).slice(1)} day="2026-10-16" sources={sources} timezone="UTC" onChat={() => {}} onCorrect={() => {}} compact={false} expanded />);
    expect(html).toContain('class="recap-topic-summary is-collapsed"');
    expect(html).toContain('A test with dark chocolate.');
    expect(html).not.toContain("1 point");
    expect(html).not.toContain("recap-item-button");
    expect(html).toContain('aria-label="Copy summary: Cocoa"');
    expect(html).toContain("Show original passages");
    expect(html).not.toContain("Loading recordings…");
    expect(html).toContain("Milk frother and cocoa.");
  });

  it("starts every topic collapsed with independent accessible inline controls and no mounted passage UI", () => {
    const prose = "A detailed account of the relevant conversation and its useful context. ".repeat(7);
    const html = renderToStaticMarkup(<RecapContent text={`## Cocoa\n${prose}\n## Garden\n${prose}`}
      day="2026-10-16" version="test" sources={sources} timezone="UTC" onCorrect={() => {}} />);
    const controls = [...html.matchAll(/class="recap-topic-option" aria-expanded="false" aria-controls="([^"]+)"/g)];
    expect(controls).toHaveLength(2);
    expect(new Set(controls.map((match) => match[1])).size).toBe(2);
    expect(html).not.toContain('class="recap-topic-summary');
    expect(html).not.toContain("Read more");
    expect(html).not.toContain("Show original passages");
    expect(html).not.toContain("Loading recordings…");
    expect(html).not.toContain('<article class="recap-evidence">');
    expect(html).not.toContain("<audio");
  });

  it("renders an expanded topic inline once while sibling rows remain visible and passages stay closed", () => {
    const parsed = recapTopics(recapParts("## Cocoa\nA cocoa trial.\n## Garden\nCompared garden tools."), "2026-10-16").topics;
    const rows = recapBrowserTopics(parsed, []).map((topic) => ({ ...topic, displayTitle: topic.title, displayParts: topic.parts }));
    const html = renderToStaticMarkup(<div className="recap-topic-list">{rows.map((topic, index) => <RecapTopicRow key={topic.id}
      topic={topic} expanded={index === 0} onToggle={() => {}} day="2026-10-16" sources={sources} timezone="UTC" onCorrect={() => {}} />)}</div>);
    expect(html.match(/class="recap-topic-row"/g)).toHaveLength(2);
    expect(html).toMatch(/data-topic-id="recap:0:Cocoa"[\s\S]*aria-expanded="true"[\s\S]*recap-topic-inline[\s\S]*data-topic-id="recap:1:Garden"[\s\S]*aria-expanded="false"/);
    expect(html.match(/A cocoa trial\./g)).toHaveLength(1);
    expect(html).toContain("Compared garden tools.");
    expect(html).not.toContain("Read more");
    expect(html).not.toContain("All topics");
    expect(html).toMatch(/aria-expanded="false" aria-controls="[^"]+">Show original passages/);
    expect(html).not.toContain("Loading recordings…");
    expect(html).not.toContain("<audio");
  });

  it.each([false, true])("keeps original passages optional independently of summary/detail expansion (detail=%s)", (expanded) => {
    const text = "The useful outcome needs some explanation and context. ".repeat(8);
    const html = renderToStaticMarkup(<RecapTopic title="Cocoa" parts={[{ kind: "text", text }]}
      day="2026-10-16" sources={sources} timezone="UTC" onCorrect={() => {}} compact={false} expanded={expanded} />);
    expect(html).toContain("Read more");
    expect(html).toContain("Show original passages");
    expect(html).toMatch(/aria-expanded="false" aria-controls="[^"]+">Show original passages/);
    expect(html).toMatch(/<div id="[^"]+" hidden="">/);
    expect(html).not.toContain("Loading recordings…");
    expect(html).not.toContain('<article class="recap-evidence">');
    expect(html).not.toContain("<audio");
  });

  it("does not mount passage UI for a selected original-only topic until explicitly requested", () => {
    const source: Source = { ...sources[0], text: "No.", sections: [{ title: "Reply", start_offset: 0, end_offset: 3, start_seconds: 1, end_seconds: 2 }] };
    const original = recapBrowserTopics([], [source])[0];
    const html = renderToStaticMarkup(<RecapTopic {...original} day="2026-10-16" sources={[source]} timezone="UTC"
      onCorrect={() => {}} compact={false} expanded onFocus={() => {}} />);
    expect(html).toContain("Show original passages");
    expect(html).not.toContain("Loading recordings…");
    expect(html).not.toContain("No.");
    expect(html).not.toContain("recap-topic-summary");
    expect(html).not.toContain("<audio");
  });

  it("copies the complete topic text, removing citations and exact repeated paragraphs", () => {
    const parts = recapParts(`A useful detail [${sources[0].id}].\n- A useful detail [${sources[0].id}].\n- Another useful detail.`);
    expect(recapTopicText(parts)).toBe("A useful detail.\n\nAnother useful detail.");
  });

  it("reveals the matching original passage without repeating unrelated recording topics", () => {
    const cocoa = "☕️ Tested a milk frother with cocoa. ";
    const tesla = "We discussed the Tesla Roadster.";
    const source: Source = { ...sources[0], text: cocoa + tesla, sections: [
      { title: "Cocoa and milk frother", start_offset: 0, end_offset: Array.from(cocoa).length },
      { title: "Tesla Roadster", start_offset: Array.from(cocoa).length, end_offset: Array.from(cocoa + tesla).length },
    ] };
    const point = `Tested a milk frother with cocoa [${source.id}]`;
    expect(recapSourceSections(point, source)).toEqual([source.sections![0]]);
    const html = renderToStaticMarkup(<RecapEvidence source={source} point={point} timezone="UTC" onCorrect={() => {}} />);
    expect(html).toContain(cocoa);
    expect(html).not.toContain(tesla);
    expect(html).not.toContain("topic sections");
    expect(html).not.toContain("source-row");
    expect(html).toContain("Full recording &amp; tools");
  });

  it("does not expose unrelated transcript text when no passage can be identified", () => {
    const source = { ...sources[0], sections: [{ title: "Gym", start_offset: 0, end_offset: 14 }] };
    const html = renderToStaticMarkup(<RecapEvidence source={source} point={`Unrelated point [${source.id}]`} timezone="UTC" onCorrect={() => {}} />);
    expect(html).toContain("no specific topic passage identified");
    expect(html).not.toContain("Gym discussion");
    expect(html).toContain("Full recording &amp; tools");
    expect(recapSourceSections("Gym", { ...source, text: null })).toEqual([]);
  });

  it("does not match another Danish topic merely because generic words overlap", () => {
    const omi = "OMI lydoptager: Det kan optage lyd på arbejdet og give et recap af dagen. ";
    const tesla = "Tesla Roadster: Bilen kan køre på vægge og loftet ifølge samtalen.";
    const source: Source = { ...sources[0], text: omi + tesla, sections: [
      { title: "Oplevelse af OMI lydoptager", start_offset: 0, end_offset: Array.from(omi).length },
      { title: "Diskussion af Tesla Roadster", start_offset: Array.from(omi).length, end_offset: Array.from(omi + tesla).length },
    ] };
    const point = `Tesla Roadster\nDet nævnes, at bilen kan køre på vægge og loftet [${source.id}]`;
    expect(recapSourceSections(point, source)).toEqual([source.sections![1]]);
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
    const laterGym = { ...sources[0], id: "later-gym", started_at: "2026-10-16T16:00:00Z", text: "A second gym discussion" };
    expect(itemSources("**Gym · 07:15**", [...sources, laterGym]).sources).toEqual([sources[0], laterGym]);
  });

  it("never falls back to unrelated recordings, even at a matching time", () => {
    expect(itemSources("**Dentist · 07:15** — Arrange a dental appointment.", sources).sources).toEqual([]);
    expect(itemSources("General reflection", sources).sources).toEqual([]);
    expect(itemSources("**Gym**", [{ ...sources[1], text: "A work discussion about gymnasium renovation." }]).sources).toEqual([]);
    expect(itemSources("**Gym**", [{ ...sources[0], text: null }]).sources).toEqual([]);
  });

  it("finds recordings for descriptive subjects and follow-ups using distinctive words", () => {
    const errands = { ...sources[0], text: "The bike needs a service. I will call the repair shop." };
    expect(itemSources("**Around town** — Book a bike service.", [errands, sources[1]]).sources).toEqual([errands]);
    expect(itemSources("call the bike shop", [errands, sources[1]]).sources).toEqual([errands]);
    const work = { ...sources[1], text: "I will share the prototype on Monday." };
    expect(itemSources("share the prototype", [errands, work]).sources).toEqual([work]);
    expect(itemSources("Review the plans for next week.", [{ ...sources[0], text: "We made plans for next week about something else." }]).sources).toEqual([]);
  });

  it("keeps recorded follow-ups readable without independent checkmarks and escapes model text", () => {
    const html = renderToStaticMarkup(<RecapContent
      text={'- **Gym · 07:15** — A workout.\n\n## Follow-ups\n- [x] <script>alert("unsafe")</script>'}
      day="2026-10-16" version="test" sources={sources} timezone="UTC" onChat={() => {}} onCorrect={() => {}}
    />);
    expect(html).toContain('data-topic-id="recap:0:Gym · 07:15"');
    expect(html).not.toContain('type="checkbox"');
    expect(html).toContain("Follow-ups mentioned in the recap");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  it("adapts flat legacy topics and preserves narrative and follow-up text", () => {
    const result = recapTopics(recapParts("A useful day.\n- **Gym** — Workout with Sam.\n- **Family** — Lunch plans.\n## Follow-ups\n- Call Sam"), "2026-10-16");
    expect(result.topics.map((topic) => topic.title)).toEqual(["Gym", "Family"]);
    expect(recapTopicText(result.introduction)).toBe("A useful day.");
    expect(result.followups.map((part) => part.text)).toEqual(["Call Sam"]);
  });

  it("scopes actions to the selected topic's character boundaries within a shared recording", () => {
    const source = { ...sources[0], text: "Cocoa discussion. Car discussion.", sections: [
      { title: "Cocoa", start_offset: 0, end_offset: 18 },
      { title: "Car", start_offset: 18, end_offset: 33 },
    ] };
    expect(topicFocus({ title: "Cocoa", parts: [{ kind: "text", text: `A cocoa discussion [${source.id}]` }] }, [source]))
      .toEqual({ title: "Cocoa", passages: [{ source_id: source.id, start: 0, end: 18 }] });
    expect(topicFocus({ title: "Unknown", parts: [{ kind: "text", text: "Unrelated." }] }, [source]).passages).toEqual([]);
  });

  it("separates legacy subjects inside broad headings and keeps their continuation text", () => {
    const result = recapTopics(recapParts("## Highlights\n- **Gym** — Workout with Sam.\nThe next session is Saturday.\n- **Family** — Lunch plans."), "2026-10-16");
    expect(result.topics.map((topic) => topic.title)).toEqual(["Gym", "Family"]);
    expect(recapTopicText(result.topics[0].parts)).toContain("The next session is Saturday.");
  });

  it("keeps omitted original chapters in topic browsing without adding them to the day synthesis", () => {
    const first = "🪴 Cocoa trial. ";
    const second = "We compared garden tools.";
    const source: Source = { ...sources[0], source_version: "source-1", transcript_version: "text-1", text: first + second, sections: [
      { title: "Cocoa", start_offset: 0, end_offset: Array.from(first).length },
      { title: "Garden tools", start_offset: Array.from(first).length, end_offset: Array.from(first + second).length },
    ] };
    const text = `## Cocoa\nA useful cocoa trial [${source.id}].`;
    const synthesized = recapTopics(recapParts(text), "2026-10-16").topics;
    const topics = recapBrowserTopics(synthesized, [source]);
    expect(topics.map((topic) => topic.title)).toEqual(["Cocoa", "Garden tools"]);
    expect(topics[1].parts).toEqual([]);
    expect(topics[1].originalPassages).toEqual([{ source_id: source.id, source_version: "source-1", transcript_version: "text-1",
      section: source.sections![1], text: second }]);
    const html = renderToStaticMarkup(<RecapContent text={text} day="2026-10-16" version="test" sources={[source]} timezone="UTC" onCorrect={() => {}} />);
    expect(html).toContain("A useful cocoa trial.");
    expect(html).toContain("Garden tools");
    expect(html).toContain("Original passages");
    expect(html).not.toContain(second);
    const separate = { ...source, id: sources[1].id, sections: [source.sections![0]] };
    expect(recapBrowserTopics(synthesized, [source, separate]).map((topic) => topic.title)).toEqual(["Cocoa", "Garden tools", "Cocoa"]);
  });

  it("groups explicitly shared original chapters across parts and rejects invalid bounds", () => {
    const one: Source = { ...sources[0], text: "First part.", sections: [
      { title: "Garden", chapter_id: "chapter-1", start_offset: 0, end_offset: 11 },
      { title: "Invalid", start_offset: 0, end_offset: 50 },
    ] };
    const two: Source = { ...sources[1], text: "Second part.", sections: [
      { title: "Garden", chapter_id: "chapter-1", start_offset: 0, end_offset: 12 },
    ] };
    const topics = recapBrowserTopics([], [one, two, one]);
    expect(topics).toHaveLength(1);
    expect(topics[0].originalPassages?.map((passage) => passage.source_id)).toEqual([one.id, two.id]);
    expect(recapBrowserTopics([], [{ ...one, state: "queued" }, { ...two, text: null }])).toEqual([]);
    expect(recapBrowserTopics([], [one, { ...two, sections: [{ ...two.sections![0], chapter_id: "chapter-2" }] }])).toHaveLength(2);
  });

  it("revalidates original section offsets, source revisions and exact quoted text before use", () => {
    const source: Source = { ...sources[0], source_version: "source-1", transcript_version: "text-1", text: "No. Later.", sections: [
      { title: "Reply", start_offset: 0, end_offset: 3, start_seconds: 2, end_seconds: 4 },
      { title: "Other", start_offset: 3, end_offset: 10 },
    ] };
    const passage = recapBrowserTopics([], [source])[0].originalPassages![0];
    const current = currentRecapPassage(passage, source)!;
    expect(current.sections).toEqual([source.sections![0]]);
    expect(currentRecapPassage(passage, { ...source, transcript_version: "text-2" })).toBeUndefined();
    expect(currentRecapPassage(passage, { ...source, source_version: "source-2" })).toBeUndefined();
    expect(currentRecapPassage(passage, { ...source, text: "Go. Later." })).toBeUndefined();
    expect(currentRecapPassage(passage, { ...source, sections: [source.sections![1]] })).toBeUndefined();
    const html = renderToStaticMarkup(<RecapEvidence source={current} point="" sections={current.sections} timezone="UTC" onCorrect={() => {}} />);
    expect(html).toContain("No.");
    expect(html).not.toContain("Later.");
    expect(html).toContain("#t=2,4");
  });
});
