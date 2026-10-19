import { source, t } from "./i18n";
import type { Decision } from "./decisionModels";
import type { InboxItem, InboxKind } from "./inboxTypes";
import type { RecapFocus } from "./recapItems";

export const highlightGroups = [
  { id: "tasks", title: source("To do"), kinds: ["commitment", "deadline"] },
  { id: "questions", title: source("Questions to answer"), kinds: ["question"] },
  { id: "plans", title: source("Events & reminders"), kinds: ["event", "reminder"] },
  { id: "reflections", title: source("Worth considering"), kinds: ["reflection"] },
  { id: "notes", title: source("Worth remembering"), kinds: ["note"] },
] as const;
export type HighlightCategory = "all" | "decisions" | typeof highlightGroups[number]["id"];

export function actionMatchesTopic(item: InboxItem, focus: RecapFocus): boolean {
  return Boolean(item.evidence_spans?.some((span) => span.current && focus.passages.some((part) =>
    part.source_id === span.source_id && span.start >= part.start && span.end <= part.end && span.end > span.start)));
}

export function decisionMatchesTopic(item: Decision, focus: RecapFocus): boolean {
  return ["current", "conflicted"].includes(item.evidence_condition) && Boolean(item.content?.choice.evidence.some((span) => focus.passages.some((part) =>
    part.source_id === span.source_id && span.start >= part.start && span.end <= part.end && span.end > span.start)));
}

export function actionableKind(kind: InboxKind): boolean { return kind !== "reflection" && kind !== "note"; }

export function highlightReviewLabel(item: InboxItem): string {
  if (item.kind === "question") return item.user_note ? t("Review answer") : t("Answer question");
  if (item.kind === "event") return t("Review event");
  if (item.kind === "reminder") return t("Review reminder");
  if (item.kind === "reflection") return t("Add your thoughts");
  if (item.kind === "note") return t("Review information");
  return t("Review action");
}

export function highlightCompletionLabel(item: InboxItem): string {
  if (item.status === "completed") return t("Reopen: {value0}", { value0: item.title });
  if (item.kind === "question") return t("Mark answered: {value0}", { value0: item.title });
  if (!actionableKind(item.kind)) return t("Mark reviewed: {value0}", { value0: item.title });
  return t("Mark complete: {value0}", { value0: item.title });
}

export function orderedHighlights(items: InboxItem[]): InboxItem[] {
  const deadline = (item: InboxItem) => item.deadline?.instant || item.deadline?.day || "9999";
  return [...items].sort((a, b) => Number(b.overdue || false) - Number(a.overdue || false)
    || Number(b.health !== "current") - Number(a.health !== "current")
    || deadline(a).localeCompare(deadline(b)) || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
}
