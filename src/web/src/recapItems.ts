import type { Source } from "./types";
import type { DisplayTranslationInput } from "./displayTranslations";
import { topicTitleKey } from "./topicOccurrences";

export type RecapPart =
  | { kind: "text"; text: string }
  | { kind: "heading"; text: string }
  | { kind: "item"; text: string; action: boolean };

export type RecapFocus = {
  title: string;
  displayTitle?: string;
  basis?: "original";
  passages: { source_id: string; start: number; end: number }[];
};
export type RecapTopicGroup = { title: string; parts: RecapPart[] };
export type OriginalRecapPassage = {
  source_id: string; source_version?: string; transcript_version?: string;
  section: NonNullable<Source["sections"]>[number]; text: string;
};
export type RecapBrowserTopic = RecapTopicGroup & { id: string; originalPassages?: OriginalRecapPassage[]; evidencePassages?: OriginalRecapPassage[] };

export function originalTopicTranslation(topic: RecapBrowserTopic): DisplayTranslationInput | undefined {
  const passage = topic.originalPassages?.[0];
  if (!passage?.source_version || !passage.transcript_version) return;
  return { kind: "source_topic", id: passage.source_id, source_version: passage.source_version,
    transcript_version: passage.transcript_version, start_offset: passage.section.start_offset,
    end_offset: passage.section.end_offset, ...(passage.section.chapter_id ? { chapter_id: passage.section.chapter_id } : {}),
    original_title: passage.section.title };
}

// Older recaps used bold labels in a flat list instead of topic headings.
export function recapTopics(parts: RecapPart[], day: string, displayParts = parts) {
  const topics: RecapTopicGroup[] = [];
  const introduction: RecapPart[] = [];
  const followups: RecapPart[] = [];
  const byTitle = new Map<string, RecapTopicGroup>();
  function append(canonicalTitle: string, title: string, parts: RecapPart[]) {
    const key = topicTitleKey(canonicalTitle);
    let topic = byTitle.get(key);
    if (!topic) { topic = { title, parts: [] }; topics.push(topic); byTitle.set(key, topic); }
    topic.parts.push(...parts);
    return topic;
  }
  const displays = new Map(parts.map((part, index) => [part, displayParts[index] || part]));
  const displayGroups = recapGroups(parts, day, displayParts);
  for (const [index, group] of recapGroups(parts, day).entries()) {
    const narrative = group.parts.filter((part) => part.kind !== "item" || !part.action);
    followups.push(...group.parts.filter((part) => part.kind === "item" && part.action).map((part) => displays.get(part)!));
    const labelled = narrative.some((part) => part.kind === "item" && /^\*\*[^*]+\*\*/.test(part.text));
    if (group.title && narrative.length && !labelled) {
      append(group.title.replace(/\*\*/g, ""), displayGroups[index].title!.replace(/\*\*/g, ""), narrative.map((part) => displays.get(part)!));
      continue;
    }
    let current: RecapTopicGroup | undefined;
    for (const part of narrative) {
      const label = part.kind === "item" ? part.text.match(/^\*\*([^*]+)\*\*\s*(?:[—–:-]\s*)?([\s\S]*)$/) : null;
      const display = displays.get(part)!;
      if (label) {
        const displayLabel = display.text.match(/^\*\*([^*]+)\*\*\s*(?:[—–:-]\s*)?([\s\S]*)$/);
        current = append(label[1], displayLabel?.[1] || label[1], [{ kind: "text", text: displayLabel?.[2] || display.text }]);
      } else if (current) current.parts.push(display);
      else introduction.push(display);
    }
  }
  return { topics, introduction, followups };
}

export function topicFocus(topic: RecapTopicGroup, sources: Source[]): RecapFocus {
  const text = topic.parts.map((part) => part.text).join("\n");
  return { title: topic.title, passages: itemSources(`**${topic.title}**\n${text}`, sources).sources.flatMap((source) =>
    recapSourceSections(text, source, topic.title).map((section) => ({ source_id: source.id, start: section.start_offset, end: section.end_offset }))) };
}

// The day synthesis is selective; original chapters remain available for deliberate exploration.
export function recapBrowserTopics(topics: RecapTopicGroup[], sources: Source[]): RecapBrowserTopic[] {
  const result: RecapBrowserTopic[] = topics.map((topic, index) => ({ ...topic, id: `recap:${index}:${topic.title}` }));
  const represented = result.map((topic) => ({ topic, passages: topicFocus(topic, sources).passages }));
  const originals = new Map<string, RecapBrowserTopic>();
  const seen = new Set<string>();
  for (const source of sources) {
    if (source.state !== "done" || !source.text) continue;
    const characters = Array.from(source.text);
    for (const section of source.sections || []) {
      if (!section.title.trim() || !Number.isInteger(section.start_offset) || !Number.isInteger(section.end_offset)
        || section.start_offset < 0 || section.end_offset <= section.start_offset || section.end_offset > characters.length) continue;
      const identity = `${source.id}:${source.source_version || ""}:${source.transcript_version || ""}:${section.start_offset}:${section.end_offset}`;
      if (seen.has(identity)) continue;
      seen.add(identity);
      const passage: OriginalRecapPassage = { source_id: source.id, source_version: source.source_version,
        transcript_version: source.transcript_version, section,
        text: characters.slice(section.start_offset, section.end_offset).join("") };
      const synthesized = represented.find(({ passages }) => passages.some((span) => span.source_id === source.id
        && span.start <= section.start_offset && span.end >= section.end_offset))?.topic
        || result.find((topic) => topicTitleKey(topic.title) === topicTitleKey(section.title));
      if (synthesized) { (synthesized.evidencePassages ??= []).push(passage); continue; }
      // Recap browsing groups the day's matching titles while preserving each source span.
      const chapter = section.chapter_id || identity;
      const key = topicTitleKey(section.title);
      const existing = originals.get(key);
      if (existing) existing.originalPassages!.push(passage);
      else originals.set(key, { id: `original:${chapter}`, title: section.title, parts: [], originalPassages: [passage] });
    }
  }
  return [...result, ...originals.values()];
}

export function currentRecapPassage(passage: OriginalRecapPassage, source: Source): Source | undefined {
  if (source.id !== passage.source_id || source.state !== "done"
    || (passage.source_version && source.source_version !== passage.source_version)
    || (passage.transcript_version && source.transcript_version !== passage.transcript_version)) return;
  const section = source.sections?.find((candidate) => candidate.start_offset === passage.section.start_offset
    && candidate.end_offset === passage.section.end_offset && candidate.title === passage.section.title
    && candidate.chapter_id === passage.section.chapter_id);
  if (!section || Array.from(source.text || "").slice(section.start_offset, section.end_offset).join("") !== passage.text) return;
  return { ...source, sections: [section] };
}

// Original-only topics still need readable descriptions when the selective recap is empty.
// Keep these as labeled excerpts, and reject text from changed or expired sources.
export function originalRecapText(passages: OriginalRecapPassage[] | undefined, sources: Source[]): string {
  return (passages || []).filter((passage) => {
    const source = sources.find((candidate) => candidate.id === passage.source_id);
    return source && currentRecapPassage(passage, source);
  }).map((passage) => passage.text.trim()).filter(Boolean).join("\n\n");
}

// Group the reading view without rewriting saved text or its importance keys.
export function recapGroups(parts: RecapPart[], day: string, displayParts = parts) {
  const groups: { title?: string; parts: RecapPart[] }[] = [];
  for (const [index, part] of parts.entries()) {
    const display = displayParts[index] || part;
    const title = part.text.replace(/\*\*/g, "").trim();
    const isHeading = part.kind === "heading" || (part.kind === "text" && /^\*\*[^*]+\*\*:?$/.test(part.text));
    if (isHeading) {
      if (!groups.length && new RegExp(`^(?:diary recap|day recap|daily recap|recap)\\s*[:·—-]?\\s*${day}$`, "i").test(title)) continue;
      groups.push({ title: display.text, parts: [] });
    } else {
      if (!groups.length) groups.push({ parts: [] });
      groups[groups.length - 1].parts.push(display);
    }
  }
  return groups.filter((group) => group.parts.length > 0);
}

export function recapReadingText(text: string) {
  const uuid = "[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}";
  return text.replace(new RegExp(`\\[\\s*${uuid}(?:\\s*,\\s*${uuid})*\\s*\\]`, "gi"), "")
    .replace(/ +([.,;!?])/g, "$1").trim();
}

export function recapGroupSummary(parts: RecapPart[]): string {
  const narrative = parts.filter((part) => part.kind === "text");
  return (narrative.length ? narrative : parts.filter((part) => part.kind === "item"))
    .map((part) => recapReadingText(part.text).replace(/\*\*/g, "")).join(" ");
}

export function recapTopicText(parts: RecapPart[]): string {
  const seen = new Set<string>();
  return parts.filter((part) => part.kind !== "heading").map((part) => recapReadingText(part.text))
    .filter((text) => {
      const key = text.replace(/\*\*/g, "").replace(/\s+/g, " ").trim().toLowerCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    }).join("\n\n");
}

const danishCommonWords = new Set("det den der dette denne disse som til med om eller og en et kan vil har var være bliver blev bruger brugeren taler nævnte nævnes beskriver beskrevet samt her også deres vores min mit mine man jeg mig dig han hun dem sin sit sine sig skal skulle kunne have havde ikke fra ved for på af at ind ud dag dagen andet første anden meget noget nogle alle hver hvad hvordan hvor hvorfor hvis når fordi optagelse optagelsen samtalen emne emnet diskussion discussion details mentioned said".split(" "));

function passageWords(text: string): string[] {
  return words(text).filter((word) => word.length >= 3 && !commonWords.has(word) && !danishCommonWords.has(word) && !/^\d+$/.test(word));
}

export function recapSourceSections(point: string, source: Source, topic = ""): NonNullable<Source["sections"]> {
  if (!source.text || !source.sections?.length) return [];
  const characters = Array.from(source.text);
  const lines = recapReadingText(point).split("\n").filter((line) => line.trim());
  const subject = topic || (lines.length > 1 ? lines[0] : "");
  const candidates = source.sections.map((section) => ({
    section, title: words(section.title).join(" "),
    text: characters.slice(section.start_offset, section.end_offset).join(""),
  }));
  const phrase = words(subject).join(" ");
  const keywords = new Set(passageWords(subject));
  if (phrase && keywords.size) {
    // The selected topic outranks incidental words shared with other passages.
    const named = candidates.filter((candidate) => ` ${candidate.title} `.includes(` ${phrase} `));
    if (named.length) return named.map((candidate) => candidate.section);
    const mentioned = candidates.filter((candidate) => ` ${words(candidate.text).join(" ")} `.includes(` ${phrase} `));
    if (mentioned.length) return mentioned.map((candidate) => candidate.section);
    const titleMatches = candidates.map((candidate) => ({ candidate,
      score: new Set(passageWords(candidate.section.title).filter((word) => keywords.has(word))).size,
    }));
    const best = Math.max(...titleMatches.map((match) => match.score));
    if (best > 0) return titleMatches.filter((match) => match.score === best).map((match) => match.candidate.section);
  }
  const terms = new Set(passageWords(recapReadingText(point)));
  const scored = candidates.map((candidate) => ({ candidate,
    score: new Set(passageWords(`${candidate.section.title} ${candidate.text}`).filter((word) => terms.has(word))).size,
  }));
  const best = Math.max(...scored.map((match) => match.score));
  const matches = scored.filter((match) => match.score === best);
  // Ambiguous keyword overlap is not sufficient to display a passage as topic evidence.
  return best >= 2 && matches.length === 1 ? [matches[0].candidate.section] : [];
}

const actionHeading = /^(?:to follow[ -]?up|follow[ -]?ups?|action (?:items?|points?)|next steps?|to[ -]?dos?|tasks?)$/i;

// Parse only presentation structure. Completion always comes from the user's checks.
export function recapParts(markdown: string, displayMarkdown?: string): RecapPart[] {
  const parts: RecapPart[] = [];
  const displayLines = displayMarkdown?.split("\n");
  let actions = false;
  for (const [index, raw] of markdown.split("\n").entries()) {
    const line = raw.trim();
    const displayLine = (displayLines?.[index] ?? raw).trim();
    if (!line) continue;
    const heading = line.match(/^#{1,4}\s+(.+)$/);
    const label = line.replace(/\*\*/g, "").match(/^([^:]+):\s*(.*)$/);
    if (heading || (label && actionHeading.test(label[1].trim())) || actionHeading.test(line.replace(/\*\*/g, "").replace(/:$/, ""))) {
      const title = heading?.[1] ?? label?.[1] ?? line.replace(/\*\*/g, "").replace(/:$/, "");
      const displayHeading = displayLine.match(/^#{1,4}\s+(.+)$/);
      const displayLabel = displayLine.replace(/\*\*/g, "").match(/^([^:]+):\s*(.*)$/);
      actions = actionHeading.test(title.replace(/\*\*/g, "").replace(/:$/, "").trim());
      parts.push({ kind: "heading", text: displayHeading?.[1] ?? displayLabel?.[1] ?? displayLine.replace(/\*\*/g, "").replace(/:$/, "") });
      if (actions && label?.[2]) {
        // Older recaps can contain a single, comma-separated follow-up paragraph.
        const body = label[2].replace(/\s+Completion is not recorded\.?$/i, "");
        // Keep commas inside a task ("email Alice, Bob and Charlie") intact.
        const items = body.split(/(?:;\s*|,\s*(?:and\s+)?)(?=(?:share|call|bring|confirm|send|book|check|prepare|schedule|review|ask|buy|pick up|follow up)\b)/i);
        for (const [itemIndex, item] of items.filter(Boolean).entries()) parts.push({ kind: "item",
          text: displayLines ? itemIndex === 0 ? displayLabel?.[2] || "" : "" : item, action: true });
      }
      continue;
    }
    const bullet = line.match(/^(?:[-*+]\s+|\d+[.)]\s+)(?:\[([ xX])\]\s+)?(.+)$/);
    if (bullet) {
      const displayBullet = displayLine.match(/^(?:[-*+]\s+|\d+[.)]\s+)(?:\[([ xX])\]\s+)?(.+)$/);
      parts.push({ kind: "item", text: displayBullet?.[2] ?? displayLine, action: actions || bullet[1] !== undefined });
    } else if (/^\s+/.test(raw) && parts.at(-1)?.kind === "item") {
      parts.at(-1)!.text += ` ${displayLine}`;
    } else {
      parts.push({ kind: "text", text: displayLine });
    }
  }
  return parts;
}

const commonWords = new Set("the and for with from that this these those about into before after next last then than will would could should have has had was were are our your their they them you its not but also some more what when where which how who been being day today tomorrow yesterday week month year time plans plan planned planning review remember recorded recording recordings discussion conversation follow share call bring confirm send book check prepare schedule ask need needs agreed suggested monday tuesday wednesday thursday friday saturday sunday".split(" "));

function words(text: string): string[] {
  return text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
}

export function itemSources(text: string, sources: Source[]): { sources: Source[]; basis: "citation" | "subject" | "words" | "unlinked" } {
  const ids = new Set((text.match(/\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b/gi) ?? []).map((id) => id.toLowerCase()));
  // Explicit source links take precedence over inferred subject matches.
  if (ids.size) return { sources: sources.filter((source) => ids.has(source.id.toLowerCase())), basis: "citation" };
  const label = text.match(/^\*\*([^*]+)\*\*/)?.[1] ?? text.match(/^([^:—–]+)[—–:]\s+/)?.[1] ?? "";
  const subject = words(label.replace(/(?:[·|]\s*)?\b\d{1,2}:\d{2}\b.*$/, ""));
  const candidates = sources.map((source) => ({ source, words: words(source.text || "") }));
  if (subject.some((word) => !commonWords.has(word) && word.length >= 3)) {
    const phrase = ` ${subject.join(" ")} `;
    const mentioned = candidates.filter((candidate) => ` ${candidate.words.join(" ")} `.includes(phrase));
    if (mentioned.length) return { sources: mentioned.map((candidate) => candidate.source), basis: "subject" };
  }
  // Uncited older recaps can have descriptive labels, e.g. "Around town".
  // Require multiple distinctive words; a timestamp alone never establishes relevance.
  const keywords = new Set(words(text).filter((word) => word.length >= 3 && !commonWords.has(word) && !/^\d+$/.test(word)));
  const pointWords = words(text);
  const pointPhrase = ` ${pointWords.join(" ")} `;
  const matching = candidates.filter((candidate) => {
    if (keywords.size && pointWords.length >= 2 && ` ${candidate.words.join(" ")} `.includes(pointPhrase)) return true;
    const overlap = [...new Set(candidate.words)].filter((word) => keywords.has(word));
    return overlap.length >= 2;
  }).map((candidate) => candidate.source);
  return { sources: matching, basis: matching.length ? "words" : "unlinked" };
}
