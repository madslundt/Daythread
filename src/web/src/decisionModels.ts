import { t } from "./i18n";
import type { Source } from "./types";

export type DecisionAnchor = {
  source_id: string; source_version: string; quote: string; start: number; end: number;
};
export type DecisionClaim = { text: string; evidence: DecisionAnchor[] };
export type DecisionContradiction = DecisionClaim & { field: "choice" | "context" | "rationale" | "alternatives" };
export type DecisionContent = {
  choice: DecisionClaim;
  context: DecisionClaim | null;
  rationale: DecisionClaim | null;
  alternatives: DecisionClaim[];
  contradictions: DecisionContradiction[];
  owner_notes: string;
  relationships: { kind: "project" | "person" | "commitment" | "meeting"; label: string; target_id: string | null }[];
};
export type Decision = {
  id: string; thread_id: string; version: number;
  state: "tentative" | "final";
  display_state: "tentative" | "final" | "revised";
  review_status: "proposed" | "confirmed" | "dismissed";
  evidence_condition: "current" | "stale" | "conflicted" | "removed";
  content: DecisionContent | null;
  started_at: string; ended_at: string; created_at: string; updated_at: string;
  predecessor_id: string | null; successor_id: string | null;
  history?: { version: number; event: string; content: DecisionContent | null; created_at: string }[];
};
export type DecisionList = { items: Decision[]; total: number };
export type DecisionExtraction = {
  id: string; status: "active" | "complete" | "stale" | "removed";
  processed_sources: number; processed_source_offset?: number; total_sources: number; pending_chunks: number; gaps: number;
  items: Decision[]; next_run_id: string | null;
  suppressed_sources?: number;
};

// Backend offsets count Unicode code points, not JavaScript UTF-16 units.
export function decisionAnchor(source: Source, quote: string): DecisionAnchor {
  if (!source.source_version || !source.text || !quote.trim())
    throw new Error(t("Choose a current transcript and a supporting quotation."));
  if (Array.from(quote).length > 20000) throw new Error(t("Choose an excerpt of at most 20,000 characters."));
  const start = source.text.indexOf(quote);
  if (start < 0) throw new Error(t("The quotation must match the transcript exactly."));
  if (source.text.indexOf(quote, start + 1) >= 0)
    throw new Error(t("This quotation occurs more than once. Include more context to identify it."));
  const offset = Array.from(source.text.slice(0, start)).length;
  return { source_id: source.id, source_version: source.source_version, quote, start: offset, end: offset + Array.from(quote).length };
}

export function blankDecisionContent(): DecisionContent {
  return { choice: { text: "", evidence: [] }, context: null, rationale: null, alternatives: [], contradictions: [], owner_notes: "", relationships: [] };
}

export function evidencePassage(source: Source, anchor: DecisionAnchor): { before: string; quote: string; after: string } | null {
  if (!source.text || source.id !== anchor.source_id || source.source_version !== anchor.source_version) return null;
  const characters = Array.from(source.text);
  if (anchor.start < 0 || anchor.end <= anchor.start || anchor.end > characters.length) return null;
  const quote = characters.slice(anchor.start, anchor.end).join("");
  if (quote !== anchor.quote) return null;
  return { before: characters.slice(0, anchor.start).join(""), quote, after: characters.slice(anchor.end).join("") };
}
