import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, request } from "./api";
import { Icon } from "./Icon";
import { useResource } from "./useResource";

export type ImportanceTarget = {
  kind: "source" | "meeting" | "label" | "recap";
  id: string;
  section?: string;
};
export const importanceKey = (target: ImportanceTarget) => JSON.stringify([target.kind, target.id, target.section || ""]);
const ImportanceContext = createContext<{
  marks?: Set<string>;
  error: string;
  pending: Set<string>;
  retry: () => void;
  save: (target: ImportanceTarget, important: boolean) => Promise<void>;
}>({ error: "", pending: new Set(), retry: () => {}, save: async () => {} });

export function ImportanceProvider({ children }: { children: ReactNode }) {
  const resource = useResource<{ items: ImportanceTarget[] }>("/v1/importance", 30_000);
  useEffect(() => {
    window.addEventListener("omi:archive-changed", resource.refresh);
    return () => window.removeEventListener("omi:archive-changed", resource.refresh);
  }, [resource.refresh]);
  const [pending, setPending] = useState(new Set<string>());
  const inFlight = useRef(new Set<string>());
  async function save(target: ImportanceTarget, important: boolean) {
    const key = importanceKey(target);
    if (inFlight.current.has(key)) return;
    inFlight.current.add(key);
    setPending(new Set(inFlight.current));
    try {
      await request("/v1/importance", { method: "PUT", body: JSON.stringify({ ...target, important }) });
      resource.setData((old) => ({ items: [
        ...(old?.items || []).filter((item) => importanceKey(item) !== key),
        ...(important ? [target] : []),
      ] }));
      resource.refresh();
    } finally {
      inFlight.current.delete(key);
      setPending(new Set(inFlight.current));
    }
  }
  return <ImportanceContext value={{
    marks: resource.data ? new Set(resource.data.items.map(importanceKey)) : undefined,
    error: resource.error, pending, retry: resource.refresh, save,
  }}>{children}</ImportanceContext>;
}

export function ImportantBadge({ target }: { target: ImportanceTarget }) {
  const { marks } = useContext(ImportanceContext);
  return marks?.has(importanceKey(target)) ? <span className="important-badge" title="Marked important" aria-label="Important"><Icon name="star" size={15} /></span> : null;
}

export function ImportanceButton({ target, label }: { target: ImportanceTarget; label: string }) {
  const { marks, pending, error: loadError, retry, save } = useContext(ImportanceContext);
  const [error, setError] = useState("");
  const important = marks?.has(importanceKey(target)) ?? false;
  const busy = pending.has(importanceKey(target));
  return <span className="importance-control">
    <button type="button" className={`text-button importance-button ${important ? "is-important" : ""}`}
      aria-label={`${important ? "Remove importance from" : "Mark as important:"} ${label}`}
      aria-pressed={important} disabled={busy || (!marks && !loadError)}
      onClick={async () => {
        if (!marks) { retry(); return; }
        setError("");
        try { await save(target, !important); }
        catch (reason) { setError(errorMessage(reason)); }
      }}>
      <Icon name="star" size={16} />
      {busy ? "Saving…" : !marks && loadError ? "Retry importance" : important ? "Important" : "Mark important"}
    </button>
    {error || (!marks && loadError) ? <span className="caption" role="alert">{error || loadError}</span> : null}
  </span>;
}
