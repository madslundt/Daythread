import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { DecisionFollowupAction, DecisionFollowups } from "./DecisionFollowups";
import { canSuggestFollowups, DecisionFollowupRequests, followupLinkLabel } from "./decisionFollowupModels";
import type { Decision } from "./decisionModels";
import { blankDecisionContent } from "./decisionModels";
import { selectProfile } from "./profiles";

const decision: Decision = { id: "decision-1", thread_id: "thread-1", version: 2, state: "final", display_state: "final",
  review_status: "confirmed", evidence_condition: "current", content: blankDecisionContent(),
  started_at: "2026-10-17T10:00:00Z", ended_at: "2026-10-17T10:01:00Z", created_at: "", updated_at: "",
  predecessor_id: null, successor_id: null };

afterEach(() => { selectProfile("default"); vi.unstubAllGlobals(); });

describe("decision follow-up requests", () => {
  it("binds inference and direct exports to the current version and profile", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ suggestions: [] })));
    vi.stubGlobal("fetch", fetch);
    selectProfile("alice");
    const requests = new DecisionFollowupRequests(decision);
    await requests.run("suggestions");
    expect(fetch.mock.calls[0][0]).toBe("/v1/decisions/decision-1/suggestions");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ expected_version: 2 });
    expect(new Headers(fetch.mock.calls[0][1].headers).get("X-Omi-Profile")).toBe("alice");
    const suggestion = { kind: "reminder" as const, title: "Send report", notes: "", reason: "Explicit next step", start_at: null, end_at: null, due_at: null, all_day: false, location: "" };
    await requests.run("calendar-export", suggestion);
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ expected_version: 2, suggestion });
  });

  it("discards late inference or export after closing or correcting a decision", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    const requests = new DecisionFollowupRequests(decision);
    const pending = requests.run("suggestions");
    requests.cancel(); finish(new Response(JSON.stringify({ suggestions: [] })));
    await expect(pending).resolves.toBeUndefined();
  });

  it("prevents an old review from exporting into a newly selected archive", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    selectProfile("alice"); const requests = new DecisionFollowupRequests(decision);
    selectProfile("bob");
    await expect(requests.run("calendar-export")).resolves.toBeUndefined();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows actionable model and evidence errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Decision changed; refresh before exporting" }), { status: 409 })));
    await expect(new DecisionFollowupRequests(decision).run("suggestions")).rejects.toThrow("Decision changed");
  });
});

describe("follow-up scheduling and availability", () => {
  it("exports events directly while preserving supplied offsets and requesting defaults for missing times", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ content: "calendar" })));
    vi.stubGlobal("fetch", fetch);
    const requests = new DecisionFollowupRequests(decision);
    const suggestion = { kind: "event" as const, title: "Meet", notes: "", reason: "Agreed meeting", start_at: "2026-10-25T02:30:00+01:00", end_at: null, due_at: null, all_day: false, location: "" };
    await requests.run("calendar-export", suggestion);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ expected_version: 2, suggestion, use_calendar_defaults: true });
    expect(suggestion.end_at).toBeNull();
  });

  it("hides undated suggestions instead of showing generic add links", () => {
    const suggestion = { kind: "event" as const, title: "Launch planning", notes: "Bring the checklist", reason: "Agreed next step", start_at: null, end_at: null, due_at: null, all_day: false, location: "" };
    for (const kind of ["event", "reminder"] as const) {
      const html = renderToStaticMarkup(<DecisionFollowupAction suggestion={{ ...suggestion, kind }} requests={new DecisionFollowupRequests(decision)} timezone="Europe/Copenhagen" />);
      expect(html).toBe("");
      expect(followupLinkLabel({ ...suggestion, kind }, "Europe/Copenhagen")).toBe("");
    }
  });

  it("hides suggestions for stale, dismissed, removed and superseded records", () => {
    expect(canSuggestFollowups(decision)).toBe(true);
    for (const patch of [{ evidence_condition: "stale" }, { evidence_condition: "conflicted" }, { review_status: "dismissed" }, { content: null }, { successor_id: "newer" }] as Partial<Decision>[]) {
      expect(canSuggestFollowups({ ...decision, ...patch })).toBe(false);
      expect(renderToStaticMarkup(<DecisionFollowups decision={{ ...decision, ...patch }} timezone="Europe/Copenhagen" />)).toBe("");
    }
  });

  it("has no generic reminder or calendar actions before dates are detected", () => {
    const html = renderToStaticMarkup(<p>Review our plan.<DecisionFollowups decision={decision} timezone="Europe/Copenhagen" /></p>);
    expect(html).toBe("<p>Review our plan.</p>");
  });

  it("turns known event and reminder dates into the link labels", () => {
    const event = { kind: "event" as const, title: "Planning", notes: "", reason: "", start_at: "2026-10-18T10:00:00+02:00", end_at: null, due_at: null, all_day: false, location: "" };
    expect(followupLinkLabel(event, "Europe/Copenhagen")).toContain("18 Oct 2026");
    expect(followupLinkLabel(event, "Europe/Copenhagen")).toContain("10:00");
    const reminder = { ...event, kind: "reminder" as const, start_at: null, due_at: event.start_at };
    expect(followupLinkLabel(reminder, "Europe/Copenhagen")).toContain("18 Oct 2026");
    const html = renderToStaticMarkup(<DecisionFollowupAction suggestion={event} requests={new DecisionFollowupRequests(decision)} timezone="Europe/Copenhagen" />);
    expect(html).toContain("18 Oct 2026");
    expect(html).toContain('aria-label="Add to calendar: Planning"');
    expect(html).not.toContain("Open in calendar");
    const reminderHtml = renderToStaticMarkup(<DecisionFollowupAction suggestion={reminder} requests={new DecisionFollowupRequests(decision)} timezone="Europe/Copenhagen" />);
    expect(reminderHtml).toContain("18 Oct 2026");
    expect(reminderHtml).not.toContain("Remind me");
    expect(reminderHtml).not.toMatch(/<article|<form|<input|<textarea|<h[1-6]/);
  });

  it("preserves all-day date labels across timezones and DST offsets for timed links", () => {
    const event = { kind: "event" as const, title: "Planning", notes: "", reason: "", start_at: "2026-10-17T00:00:00+14:00", end_at: null, due_at: null, all_day: true, location: "" };
    expect(followupLinkLabel(event, "America/Los_Angeles")).toBe("17 Oct 2026");
    expect(followupLinkLabel({ ...event, all_day: false, start_at: "2026-10-25T02:30:00+01:00" }, "Europe/Copenhagen")).toContain("02:30");
  });
});
