import { t } from "./i18n";
export type InboxStatus = "pending" | "completed" | "dismissed";
export type InboxKind = "commitment" | "deadline" | "question" | "reminder" | "event" | "reflection" | "note";
export interface Deadline {
  precision: "unknown" | "date" | "datetime";
  day?: string | null;
  instant?: string | null;
  timezone?: string | null;
  phrase?: string | null;
  basis?: string | null;
}
export interface InboxFields {
  title: string;
  kind: InboxKind;
  owner: string | null;
  deadline: Deadline | null;
  user_note?: string | null;
}
export interface InboxItem extends InboxFields {
  id: string;
  status: InboxStatus;
  health: "current" | "needs_review" | "withdrawn";
  version: number;
  created_at: string;
  updated_at: string;
  reviewed_at: string | null;
  user_note_provenance?: "user" | null;
  duplicate_of: string | null;
  overdue?: boolean;
  source_ids?: string[];
  evidence_spans?: { source_id: string; start: number; end: number; current: boolean }[];
  proposal: InboxFields;
  overrides: Partial<InboxFields>;
  evidence?: {
    source_id: string;
    revision: string;
    start: number;
    end: number;
    quote: string;
    started_at: string;
    audio_available: boolean;
    current: boolean;
    timing: string;
  }[];
  history?: { id: string; kind: string; created_at: string }[];
}
export interface InboxPage {
  items: InboxItem[];
  total: number;
  pending: number;
  coverage: { jobs: Record<string, number>; unscanned_sources: number };
  recent_jobs: {
    id: string;
    state: string;
    attempts: number;
    error: string | null;
    recording_start: string | null;
  }[];
}
export function deadlineLabel(value: Deadline | null): string {
  if (!value || value.precision === "unknown")
    return value?.phrase ? t("Unresolved: {value0}", { value0: value.phrase }) : t("Deadline unknown");
  return value.precision === "date"
    ? value.day!
    : `${value.instant} (${value.timezone})`;
}
