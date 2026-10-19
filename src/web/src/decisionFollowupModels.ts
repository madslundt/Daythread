import { interfaceLanguage } from "./i18n";
import { request } from "./api";
import type { Decision } from "./decisionModels";
import { profileScope } from "./profiles";

export type DecisionFollowup = {
  kind: "reminder" | "event";
  title: string; notes: string; reason: string;
  start_at: string | null; end_at: string | null; due_at: string | null;
  all_day: boolean; location: string;
  location_trigger?: "arrive" | "leave" | null;
};
export type DecisionSuggestions = {
  decision_id: string; decision_version: number; timezone: string;
  suggestions: DecisionFollowup[];
};
export type CalendarFile = { filename: string; content: string };

export function canSuggestFollowups(decision: Decision): boolean {
  return Boolean(decision.content && decision.evidence_condition === "current"
    && decision.review_status !== "dismissed" && !decision.successor_id);
}

export function followupLinkLabel(suggestion: DecisionFollowup, timezone: string): string {
  const value = suggestion.kind === "event" ? suggestion.start_at : suggestion.due_at;
  let date = "";
  if (value) {
    const instant = new Date(suggestion.all_day ? `${value.slice(0, 10)}T12:00:00Z` : value);
    date = new Intl.DateTimeFormat(interfaceLanguage() === "en" ? "en-GB" : interfaceLanguage(), {
      day: "numeric", month: "short", year: "numeric",
      ...(suggestion.all_day ? {} : { hour: "2-digit", minute: "2-digit" }),
      timeZone: suggestion.all_day ? "UTC" : timezone,
    }).format(instant);
  }
  return date;
}

// Bind each action to its original profile and cancel late inference/export responses.
export class DecisionFollowupRequests {
  private scope = profileScope();
  private controller = new AbortController();
  constructor(private decision: Pick<Decision, "id" | "version">, private resource: "decisions" | "inbox" = "decisions") {}
  cancel(): void { this.controller.abort(); }
  async run<T>(action: "suggestions" | "calendar-export", suggestion?: DecisionFollowup): Promise<T | undefined> {
    if (this.controller.signal.aborted || this.scope.signal.aborted) return undefined;
    try {
      const result = await request<T>(`/v1/${this.resource}/${encodeURIComponent(this.decision.id)}/${action}`, {
        method: "POST", signal: this.controller.signal,
        body: JSON.stringify({ expected_version: this.decision.version, ...(suggestion ? { suggestion, ...(suggestion.kind === "event" ? { use_calendar_defaults: true } : {}) } : {}) }),
      }, this.scope);
      if (!this.controller.signal.aborted && !this.scope.signal.aborted) return result;
    } catch (error) {
      if (!this.controller.signal.aborted && !this.scope.signal.aborted) throw error;
    }
  }
}
