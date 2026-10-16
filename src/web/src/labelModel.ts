import type { Decision } from "./decisionModels";
import { useRef, useState } from "react";
import { errorMessage, newId, post } from "./api";
import type { Source } from "./types";

export type Entity = {
  id: string;
  kind: "person" | "project";
  name: string;
  description: string;
  aliases: string[];
};
export type EntityDraft = Omit<Entity, "id">;
export type SourceRef = { id: string; revision: string };
export type LabelLink = {
  id: string;
  entity_id: string;
  source_id: string;
  relation: string;
  needs_review: boolean;
};
export type LabelEntry = Partial<LabelLink> & {
  id: string;
  entry_type: "source" | "note" | "decision";
  decision_id?: string;
  decision?: Decision;
  kind?: string;
  title?: string;
  body?: string;
  entity_ids?: string[];
  sources: Source[];
  started_at: string;
  ended_at: string;
  needs_review: boolean;
};
export type Directory = { revision: string; items: Entity[] };
export type EntityTimeline = {
  revision: string;
  entity: Entity;
  items: LabelEntry[];
  total: number;
};
export const sourceRef = (source: Source): SourceRef => ({
  id: source.id,
  revision: source.source_version || "",
});
export const meanings: Record<string, string> = {
  related: "Related · no participation claim",
  mentioned: "Mentioned",
  participant: "Participant · confirmed by you",
  about_project: "About project",
};
export const kinds: Record<string, string> = {
  conversation: "Conversation · grouped by you",
  decision: "Decision note · personal annotation, separate from register",
  open_question: "Open question · written by you",
};

export function useLabelMutation(onSaved: (action?: string) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<{ signature: string; id: string } | null>(null);
  async function mutate(value: Record<string, unknown>) {
    if (busy) return;
    const signature = JSON.stringify(value);
    if (pending.current?.signature !== signature)
      pending.current = { signature, id: newId() };
    setBusy(true);
    setError("");
    try {
      await post("/v1/labels/mutations", {
        ...value,
        mutation_id: pending.current.id,
      });
      pending.current = null;
      window.dispatchEvent(new Event("omi:archive-changed"));
      onSaved(value.action as string);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, mutate };
}
