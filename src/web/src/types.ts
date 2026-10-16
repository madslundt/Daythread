export type Source = {
  id: string;
  device_id: string;
  source_version?: string;
  started_at: string;
  ended_at: string;
  state: string;
  text: string | null;
  important?: boolean;
  audio_available?: boolean;
  source_kind?: string;
  source_id?: string | null;
  transcript_version?: string;
  matches?: { text: string; start_offset: number; end_offset: number; timing: string }[];
  meeting_hint_ids?: string[];
};
export type Marker = {
  event_id: string;
  occurred_at: string;
  context_start: string;
  context_end: string;
};
export type Meeting = {
  id: string;
  started_at: string;
  ended_at: string | null;
  auto_stop_at: string;
  qualifies_at: string;
  status: string;
  context_start: string;
  context_end: string;
  end_reason?: string;
};
export type Timeline = {
  markers: Marker[];
  meetings: Meeting[];
  active_meeting: Meeting | null;
};
export type Status = {
  audio: Record<string, number>;
  timezone: string;
  worker_enabled: boolean;
  worker_running: boolean;
  meeting_max_minutes: number;
  capture_paused: boolean;
  hint_policy: { marker_cooldown_seconds: number; meeting_min_seconds: number };
};
export type MemoryWindow = {
  start: string;
  end: string;
  sources: Source[];
  pending_chunks: number;
  audio_gaps: { start: string; end: string }[];
  summary: string | null;
  markers: Marker[];
  meetings: Meeting[];
};
export type Recap = Partial<MemoryWindow> & {
  day: string;
  summary: string | null;
  generated_at?: string;
  pending_chunks?: number;
};
export type CalendarDay = {
  day: string;
  has_summary: boolean;
};
export type CalendarMonth = {
  month: string;
  days: CalendarDay[];
};
export type ChatSession = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  day?: string | null;
  meeting_id?: string | null;
  start_day?: string | null;
  end_day?: string | null;
};
export type ChatScope =
  | { kind: "all" }
  | { kind: "day"; day: string }
  | { kind: "range"; start_day: string; end_day: string }
  | { kind: "meeting"; meeting_id: string };
export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  stale?: boolean;
  retrieval_details?: { notice?: string } | null;
  content: string;
  sources: Source[];
  created_at: string;
};
export type Conversation = ChatSession & { messages: ChatMessage[] };
export type Settings = {
  timezone: string;
  language: string;
  meeting_max_minutes: number;
  meeting_min_seconds: number;
  meeting_context_seconds: number;
  marker_context_seconds: number;
  marker_after_seconds: number;
  marker_cooldown_seconds: number;
  recap_hour: number;
  capture_paused: boolean;
  audio_retention_days: number;
  transcript_retention_days: number;
};
