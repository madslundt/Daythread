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
  return groups;
}

export function recapReadingText(text: string) {
  return text.replace(/\[[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\]/gi, "").replace(/ +([.,;!?])/g, "$1").trim();
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
