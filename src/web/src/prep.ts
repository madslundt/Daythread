import { source } from "./i18n";
import type { Source } from "./types";
export type PrepSearch = {
  kind: "person" | "project" | "topic";
  subject: string;
  aliases: string[];
  qualifier: string;
  start_day: string | null;
  end_day: string | null;
  offset: number;
};
export type PrepSource = Source & { version: string };
export type PrepEvidence = {
  source_id: string;
  excerpt: string;
  version: string;
  started_at: string;
  ended_at: string;
  segment_start: number | null;
  segment_end: number | null;
};
export type PrepItem = {
  text: string;
  citations: PrepEvidence[];
  completion_status: "unknown" | null;
  owner: null;
};
export type PrepSearchResult = {
  as_of: string;
  search: PrepSearch;
  snapshot: string;
  timezone: string;
  sources: PrepSource[];
  coverage: {
    total_matches: number;
    pending_chunks: number;
    capture_gaps: number;
    returned: number;
    omitted_matches: number;
    limitations: string[];
  };
};
export type PrepResult = PrepSearchResult & {
  reviewed_excerpts: {
    id: string;
    text: string;
    started_at: string;
    omitted_characters: number;
  }[];
  generated_at: string;
  model: string;
  prompt_version: string;
  latest_source_id: string;
  user_context: string;
  latest_discussion: PrepItem[];
  promises_to_check: PrepItem[];
  recorded_questions: PrepItem[];
  suggested_questions: PrepItem[];
};
export const prepSections = [
  ["latest_discussion", source("Latest confirmed recording")],
  ["promises_to_check", source("Promises to check · completion and owner unknown")],
  ["recorded_questions", source("Recorded questions")],
  ["suggested_questions", source("Suggested questions · proposals based on evidence")],
] as const;
export function prepIsOld(at: string, now = Date.now()): boolean {
  return now - Date.parse(at) > 30 * 86400000;
}
