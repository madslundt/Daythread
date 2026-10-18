export type Source = {
  manual_location?: { label: string; latitude: number; longitude: number; zone_id?: string; location_id?: string; zone_name?: string };
  tags?: { id: string; name: string }[];
  alternate_sources?: Source[];
  id: string;
  device_id: string;
  source_version?: string;
  started_at: string;
  ended_at: string;
  state: string;
  text: string | null;
  title?: string | null;
  sections?: { title: string; start_offset: number; end_offset: number }[];
  important?: boolean;
  audio_available?: boolean;
  source_kind?: string;
  vendor_id?: string;
  vendor_label?: string;
  timestamp_basis?: string;
  source_metadata?: {
    vendor_id?: string;
    location?: { latitude: number; longitude: number; horizontal_accuracy: number; captured_at: string };
    input_sample_rate?: number; archive_sample_rate?: number;
    recording_id?: string; part_index?: number; part_count?: number;
    conversation_source_id?: string;
    recording_started_at?: string; recording_ended_at?: string;
  };
  audio_availability?: string;
  transcription_outcome?: string;
  transcription_details?: TranscriptionDetails;
  error_code?: string;
  source_id?: string | null;
  transcript_version?: string;
  matches?: { text: string; start_offset: number; end_offset: number; timing: string }[];
  meeting_hint_ids?: string[];
};
export type TranscriptionDetails = {
  version?: number;
  enhancement_mode?: string;
  preserve_quiet?: boolean;
  chunks?: {
    start: number;
    end: number;
    gain_db: number;
    enhancement_status: string;
    candidate?: { text: string; differs: boolean };
  }[];
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
  auto_stop_at: string | null;
  meeting_capture_device_id?: string | null;
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
  generation?: { queued: number; running: number; stages: Record<string, number> };
  as_of?: string;
  ingestion?: {
    last_received_at: string | null;
    last_captured_at: string | null;
  };
  processing?: {
    queued: number;
    transcribing: number;
    retrying: number;
    blocked: number;
    oldest_received_at: string | null;
    oldest_age_seconds?: number | null;
    last_completion_delay_seconds?: number | null;
    active?: {
      id: string;
      stage: string;
      completed_chunks: number;
      total_chunks: number;
      elapsed_seconds: number | null;
    }[];
    estimated_remaining_seconds?: number | null;
    estimate_sample_count?: number;
    recent_errors: {
      id: string;
      error_code: string;
      attempts: number;
      retry_at: string;
    }[];
  };
  phones?: {
    phone_id: string;
    device_id?: string;
    fresh: boolean;
    observed_at: string;
    capture_state: string;
    capture_source?: "omi" | "phone" | "unknown";
    cause: string;
    pending_recordings: number;
    recovery_items: number;
    pending_seconds: number;
    upload_policy: string;
    upload_state: string;
    last_decoded_at: string | null;
  }[];
  capture_gaps?: number;
  audio: Record<string, number>;
  timezone: string;
  worker_enabled: boolean;
  worker_running: boolean;
  meeting_max_minutes: number;
  capture_paused: boolean;
  hint_policy: { marker_cooldown_seconds: number; meeting_min_seconds: number };
};
export type MemoryWindow = {
  output_id?: string;
  evidence?: Evidence;
  completeness?: Completeness;
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
  stale?: boolean;
  pending_chunks?: number;
  included_through?: string | null;
  deferred_conversations?: number;
  deferred_sources?: number;
  waiting_for_pause?: number;
  pending_upload_sources?: number;
  boundary_note?: string;
  no_recap_content?: boolean;
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
  evidence?: Evidence;
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
  meeting_quiet_minutes?: number;
  timezone: string;
  language: string;
  summary_language: string;
  translation_languages?: string[];
  meeting_max_minutes: number;
  meeting_min_seconds: number;
  meeting_context_seconds: number;
  marker_context_seconds: number;
  marker_after_seconds: number;
  marker_cooldown_seconds: number;
  recap_hour: number;
  recap_interval_minutes?: number;
  recap_quiet_minutes?: number;
  capture_paused: boolean;
  audio_retention_days: number;
  transcript_retention_days: number;
};

export type Evidence = {
  generated_at: string;
  retrieval_mode: string;
  eligible_transcripts: number;
  selected: { id: string; revision: string }[];
  cited_ids: string[];
  pending_chunks: number;
  blocked_chunks: number;
  input_handling: string;
  pipeline?: {
    reduction_used: boolean;
    model_calls: number;
    final_prompt_source_ids: string[];
  } | null;
  changed_source_ids?: string[];
};
export type Completeness = {
  as_of?: string;
  observed_until?: string;
  seconds: Record<string, number>;
  counts: Record<string, number>;
  capture_gaps: {
    gap_id: string;
    started_at: string;
    ended_at: string;
    reason: string;
    cause: string;
  }[];
  capture_gap_count: number;
  capture_intervals?: {
    event_id: string;
    state: string;
    cause: string;
    start: string;
    end: string;
    open: boolean;
  }[];
  unknown_intervals: { start: string; end: string }[];
  unknown_count: number;
  truncated: boolean;
  start: string;
  end: string;
};
