import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ActionFollowup, ActionFollowups } from "./ActionFollowups";
import { ActionPoints, actionMatchesTopic } from "./ActionPoints";
import { DecisionFollowupRequests, type DecisionFollowup } from "./decisionFollowupModels";
import type { InboxItem } from "./inboxTypes";
import { selectProfile } from "./profiles";
import type { Decision } from "./decisionModels";
import { decisionMatchesTopic, highlightCompletionLabel, highlightGroups, orderedHighlights } from "./dayHighlights";

const data = vi.hoisted(() => ({ item: undefined as InboxItem | undefined, completed: [] as InboxItem[], decisions: [] as Decision[], jobs: undefined as Record<string, number> | undefined }));
vi.mock("./useResource", () => ({ useResource: (path: string) => ({ data: path.startsWith("/v1/decisions?") ? { items: data.decisions, total: data.decisions.length } : data.item || data.jobs ? {
  items: path.includes("status=completed") ? data.completed : data.item ? [data.item] : [], total: path.includes("status=completed") ? data.completed.length : data.item ? 1 : 0, pending: data.item ? 1 : 0, coverage: { jobs: data.jobs || { done: 1 }, unscanned_sources: 0 },
} : undefined, loading: false, error: "", refresh: () => {}, setData: () => {} }) }));

const fields = { title: "Buy cocoa", kind: "commitment" as const, owner: null, deadline: null };
const item: InboxItem = { ...fields, id: "action", status: "pending", health: "current", version: 2,
  created_at: "2026-10-19T10:00:00Z", updated_at: "2026-10-19T10:00:00Z", reviewed_at: null,
  duplicate_of: null, proposal: fields, overrides: {}, source_ids: ["shared"],
  evidence_spans: [{ source_id: "shared", start: 20, end: 30, current: true }],
};
const suggestion: DecisionFollowup = { kind: "reminder", title: "Buy cocoa", notes: "", reason: "Agreed errand",
  start_at: null, end_at: null, due_at: null, all_day: false, location: "" };
afterEach(() => { data.item = undefined; data.completed = []; data.decisions = []; data.jobs = undefined; vi.unstubAllGlobals(); selectProfile("default"); });

describe("recap actions", () => {
  it("uses current passage boundaries rather than a shared source alone", () => {
    expect(actionMatchesTopic(item, { title: "Cocoa", passages: [{ source_id: "shared", start: 15, end: 40 }] })).toBe(true);
    for (const evidence_spans of [undefined, [{ source_id: "shared", start: 20, end: 30, current: false }],
      [{ source_id: "shared", start: 0, end: 50, current: true }], [{ source_id: "other", start: 20, end: 30, current: true }]]) {
      expect(actionMatchesTopic({ ...item, evidence_spans }, { title: "Cocoa", passages: [{ source_id: "shared", start: 15, end: 40 }] })).toBe(false);
    }
    expect(actionMatchesTopic(item, { title: "Car", passages: [{ source_id: "shared", start: 40, end: 60 }] })).toBe(false);
  });
  it("shows actionable shared status with evidence review and an Inbox link", () => {
    data.item = item;
    const html = renderToStaticMarkup(<ActionPoints day="2026-10-19" timezone="UTC" />);
    expect(html).toContain('href="/inbox"');
    expect(html).toContain('aria-label="Mark complete: Buy cocoa"');
    expect(html).toContain("Review action");
    expect(html).toContain("Saved across your devices");
    expect(html).not.toContain("localStorage");
  });
  it("does not offer completion on changed evidence or suggestions on inactive items", () => {
    data.item = { ...item, health: "needs_review" };
    const html = renderToStaticMarkup(<ActionPoints day="2026-10-19" timezone="UTC" />);
    expect(html).toContain('disabled=""');
    expect(html).toContain("Evidence changed");
    for (const patch of [{ health: "needs_review" }, { status: "completed" }, { status: "dismissed" }] as Partial<InboxItem>[]) {
      expect(renderToStaticMarkup(<ActionFollowups item={{ ...item, ...patch }} timezone="UTC" />)).toBe("");
    }
  });
  it("uses Inbox versions and cancels requests when the profile changes", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ suggestions: [] })));
    vi.stubGlobal("fetch", fetch);
    selectProfile("alice");
    const requests = new DecisionFollowupRequests(item, "inbox");
    await requests.run("suggestions");
    expect(fetch.mock.calls[0][0]).toBe("/v1/inbox/action/suggestions");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ expected_version: 2 });
    selectProfile("bob");
    await expect(requests.run("calendar-export", suggestion)).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledOnce();
  });
  it("offers undated reminders, explicit event actions, and an iPhone handoff for missing event dates", () => {
    const render = (value: DecisionFollowup) => renderToStaticMarkup(<ActionFollowup suggestion={value} requests={new DecisionFollowupRequests(item, "inbox")} timezone="UTC" />);
    expect(render(suggestion)).toContain("Add reminder");
    expect(render({ ...suggestion, kind: "event", start_at: "2026-10-20T10:00:00Z" })).toContain("Add to calendar");
    expect(render({ ...suggestion, kind: "event" })).toContain("No event date was recorded");
    expect(render({ ...suggestion, location: "Bike shop", location_trigger: "arrive" })).toContain("Open on iPhone");
  });
  it("groups every captured kind by what the user can do with it", () => {
    expect(highlightGroups.flatMap((group) => [...group.kinds]).sort()).toEqual(["commitment", "deadline", "question", "event", "reminder", "reflection", "note"].sort());
    const cases = [
      ["commitment", "To do", "Review action"], ["question", "Questions to answer", "Answer question"],
      ["event", "Events &amp; reminders", "Review event"], ["reminder", "Events &amp; reminders", "Review reminder"],
      ["reflection", "Worth considering", "Add your thoughts"], ["note", "Worth remembering", "Review information"],
    ] as const;
    for (const [kind, heading, action] of cases) {
      data.item = { ...item, kind };
      const html = renderToStaticMarkup(<ActionPoints day="2026-10-19" timezone="UTC" onChat={() => {}} />);
      expect(html).toContain("Your day at a glance");
      expect(html).toContain(`<h3>${heading}</h3>`);
      expect(html).toContain(action);
    }
  });
  it("treats answers and personal thoughts as user annotations, without marking items complete", () => {
    data.item = { ...item, kind: "question", user_note: "Ask Ada for the final budget.", user_note_provenance: "user" };
    const html = renderToStaticMarkup(<ActionPoints day="2026-10-19" timezone="UTC" onChat={() => {}} />);
    expect(html).toContain("Your answer");
    expect(html).toContain("Ask Ada for the final budget.");
    expect(html).toContain("Review answer");
    expect(html).toContain('aria-label="Mark answered: Buy cocoa"');
    expect(html).not.toContain('checked=""');
    expect(highlightCompletionLabel({ ...item, kind: "reflection" })).toBe("Mark reviewed: Buy cocoa");
    for (const kind of ["reflection", "note"] as const) expect(renderToStaticMarkup(<ActionFollowups item={{ ...item, kind }} timezone="UTC" />)).toBe("");
  });
  it("keeps decisions alongside follow-ups and matches their actual choice evidence to topics", () => {
    const decision: Decision = { id: "decision", version: 1, thread_id: "thread", state: "tentative", display_state: "tentative", review_status: "proposed", evidence_condition: "current",
      content: { choice: { text: "Use the simpler onboarding flow", evidence: [{ source_id: "shared", source_version: "v1", quote: "We agreed.", start: 20, end: 30 }] }, context: null, rationale: null, alternatives: [], contradictions: [], owner_notes: "", relationships: [] },
      started_at: "2026-10-19T10:00:00Z", ended_at: "2026-10-19T10:01:00Z", created_at: "2026-10-19T10:00:00Z", updated_at: "2026-10-19T10:00:00Z", predecessor_id: null, successor_id: null };
    data.item = item; data.decisions = [decision];
    const html = renderToStaticMarkup(<ActionPoints day="2026-10-19" timezone="UTC" />);
    expect(html).toContain("Use the simpler onboarding flow"); expect(html).toContain("Review decision");
    expect(html).toContain("Buy cocoa"); expect(html).toContain("Tentative"); expect(html).toContain("Proposed");
    expect(decisionMatchesTopic(decision, { title: "Onboarding", passages: [{ source_id: "shared", start: 0, end: 40 }] })).toBe(true);
    expect(decisionMatchesTopic({ ...decision, evidence_condition: "conflicted" }, { title: "Onboarding", passages: [{ source_id: "shared", start: 0, end: 40 }] })).toBe(true);
    expect(decisionMatchesTopic(decision, { title: "Unrelated", passages: [{ source_id: "shared", start: 40, end: 80 }] })).toBe(false);
    expect(decisionMatchesTopic({ ...decision, evidence_condition: "stale" }, { title: "Onboarding", passages: [{ source_id: "shared", start: 0, end: 40 }] })).toBe(false);
  });
  it("keeps reviewed information and thoughts visible while completed tasks leave the open overview", () => {
    data.item = item;
    data.completed = [
      { ...item, id: "note", title: "Guests use the east entrance", kind: "note", status: "completed", user_note: "Tell visitors before they arrive." },
      { ...item, id: "reflection", title: "Reconsider interview capacity", kind: "reflection", status: "completed" },
      { ...item, id: "done-task", title: "Already sent the invitation", status: "completed" },
    ];
    const html = renderToStaticMarkup(<ActionPoints day="2026-10-19" timezone="UTC" />);
    expect(html).toContain("Guests use the east entrance");
    expect(html).toContain("Reconsider interview capacity");
    expect(html).toContain("Tell visitors before they arrive.");
    expect(html).toContain("Important information · Reviewed");
    expect(html).toContain('aria-label="Reopen: Guests use the east entrance"');
    expect(html).not.toContain("Already sent the invitation");
  });
  it("puts overdue work and changed evidence before less urgent items", () => {
    const ordinary = { ...item, id: "ordinary" }, overdue = { ...item, id: "overdue", overdue: true }, review = { ...item, id: "review", health: "needs_review" as const };
    expect(orderedHighlights([ordinary, review, overdue]).map((value) => value.id)).toEqual(["overdue", "review", "ordinary"]);
  });
  it("omits withdrawn proposals while retaining user-reviewed records that need attention", () => {
    data.item = { ...item, health: "withdrawn" };
    data.completed = [{ ...item, id: "reviewed-note", title: "Keep the access instructions",
      kind: "note", health: "needs_review", status: "completed", reviewed_at: item.updated_at }];
    const html = renderToStaticMarkup(<ActionPoints day="2026-10-19" timezone="UTC" />);
    expect(html).not.toContain("Buy cocoa");
    expect(html).toContain("Keep the access instructions");
    expect(html).toContain("Evidence changed — review required");
  });
  it("labels failed Inbox extraction separately before Decisions and offers a day retry", () => {
    data.jobs = { failed: 1 };
    const html = renderToStaticMarkup(<ActionPoints day="2026-10-19" timezone="UTC" />);
    const statusIndex = html.indexOf('aria-label="Follow-up extraction"');
    const decisionsIndex = html.indexOf('aria-label="Decisions"');
    expect(statusIndex).toBeGreaterThan(-1);
    expect(statusIndex).toBeLessThan(decisionsIndex);
    expect(html.slice(statusIndex, decisionsIndex)).toContain("Some follow-ups or notes could not be extracted.");
    expect(html.slice(statusIndex, decisionsIndex)).toContain("Retry follow-up extraction");
    expect(html.slice(decisionsIndex)).not.toContain("Some extraction failed.");
    expect(html).not.toContain("No open follow-ups or notes found in this view.");
  });
  it("does not hide a partial extraction failure when other follow-ups have been found", () => {
    data.item = item; data.jobs = { done: 1, failed: 1 };
    const html = renderToStaticMarkup(<ActionPoints day="2026-10-19" timezone="UTC" />);
    expect(html).toContain("Buy cocoa");
    expect(html).toContain("Some follow-ups or notes could not be extracted.");
  });
});
