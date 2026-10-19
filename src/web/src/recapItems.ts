import type { Source } from "./types";

export type RecapPart =
  | { kind: "text"; text: string }
  | { kind: "heading"; text: string }
  | { kind: "item"; text: string; action: boolean };

// Group the reading view without rewriting saved text or its importance keys.
export function recapGroups(parts: RecapPart[], day: string) {
  const groups: { title?: string; parts: RecapPart[] }[] = [];
  for (const part of parts) {
    const title = part.text.replace(/\*\*/g, "").trim();
    const isHeading = part.kind === "heading" || (part.kind === "text" && /^\*\*[^*]+\*\*:?$/.test(part.text));
    if (isHeading) {
      if (!groups.length && new RegExp(`^(?:diary recap|day recap|daily recap|recap)\\s*[:·—-]?\\s*${day}$`, "i").test(title)) continue;
      groups.push({ title: part.text, parts: [] });
    } else {
      if (!groups.length) groups.push({ parts: [] });
      groups[groups.length - 1].parts.push(part);
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
export function recapParts(markdown: string): RecapPart[] {
  const parts: RecapPart[] = [];
  let actions = false;
  for (const raw of markdown.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const heading = line.match(/^#{1,4}\s+(.+)$/);
    const label = line.replace(/\*\*/g, "").match(/^([^:]+):\s*(.*)$/);
    if (heading || (label && actionHeading.test(label[1].trim())) || actionHeading.test(line.replace(/\*\*/g, "").replace(/:$/, ""))) {
      const title = heading?.[1] ?? label?.[1] ?? line.replace(/\*\*/g, "").replace(/:$/, "");
      actions = actionHeading.test(title.replace(/\*\*/g, "").replace(/:$/, "").trim());
      parts.push({ kind: "heading", text: title });
      if (actions && label?.[2]) {
        // Older recaps can contain a single, comma-separated follow-up paragraph.
        const body = label[2].replace(/\s+Completion is not recorded\.?$/i, "");
        // Keep commas inside a task ("email Alice, Bob and Charlie") intact.
        const items = body.split(/(?:;\s*|,\s*(?:and\s+)?)(?=(?:share|call|bring|confirm|send|book|check|prepare|schedule|review|ask|buy|pick up|follow up)\b)/i);
        for (const item of items.filter(Boolean)) parts.push({ kind: "item", text: item, action: true });
      }
      continue;
    }
    const bullet = line.match(/^(?:[-*+]\s+|\d+[.)]\s+)(?:\[([ xX])\]\s+)?(.+)$/);
    if (bullet) {
      parts.push({ kind: "item", text: bullet[2], action: actions || bullet[1] !== undefined });
    } else if (/^\s+/.test(raw) && parts.at(-1)?.kind === "item") {
      parts.at(-1)!.text += ` ${line}`;
    } else {
      parts.push({ kind: "text", text: line });
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
