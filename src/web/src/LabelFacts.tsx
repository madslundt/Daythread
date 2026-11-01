import { useEffect, useRef, useState } from "react";
import { errorMessage, request } from "./api";
import { t } from "./i18n";
import { NoteEditor } from "./LabelEditors";
import { useLabelMutation, type EntityTimeline, type LabelEntry } from "./labelModel";
import { SourceList } from "./SourceList";
import { ErrorState } from "./UI";
import { useResource } from "./useResource";

function FactList({ entityId, timezone, history, onEdit, onSaved }: {
  entityId: string; timezone: string; history: boolean;
  onEdit: (entry: LabelEntry, revision: string) => void; onSaved: () => void;
}) {
  const path = `/v1/labels/${entityId}/facts?scope=${history ? "history" : "current"}&limit=50`;
  const result = useResource<EntityTimeline>(path);
  const [extra, setExtra] = useState<LabelEntry[]>([]);
  const [pageError, setPageError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const generation = useRef(0);
  const revision = result.data?.revision;
  useEffect(() => {
    generation.current++;
    setExtra([]); setLoadingMore(false);
  }, [revision]);
  useEffect(() => {
    const refresh = () => { generation.current++; setExtra([]); result.refresh(); };
    window.addEventListener("omi:archive-changed", refresh);
    return () => { generation.current++; window.removeEventListener("omi:archive-changed", refresh); };
  }, [result.refresh]);
  const mutation = useLabelMutation(() => { result.refresh(); onSaved(); });
  const entries = result.data ? [...result.data.items, ...extra] : [];
  async function more() {
    if (!result.data || loadingMore) return;
    const token = generation.current, expected = result.data.revision;
    setLoadingMore(true); setPageError("");
    try {
      const next = await request<EntityTimeline>(`${path}&offset=${entries.length}&revision=${expected}`);
      if (token === generation.current && next.revision === expected) setExtra((old) => [...old, ...next.items]);
    } catch (error) {
      if (token === generation.current) setPageError(errorMessage(error));
    } finally {
      if (token === generation.current) setLoadingMore(false);
    }
  }
  return <>
    {result.loading && !result.data ? <p role="status">{t("Loading facts…")}</p> : null}
    {result.data && !entries.length ? <p className="caption">{history ? t("No fact history yet.") : t("No current reviewed facts.")}</p> : null}
    {entries.map((entry) => <article className="label-panel" key={entry.id}>
      <p className="caption">{entry.fact?.status === "superseded" ? t("Replaced fact") :
        entry.fact?.status === "rejected" ? t("Rejected fact") : t("Confirmed by you")}</p>
      <h3>{entry.title}</h3><p className="transcript-text">{entry.body}</p>
      {entry.needs_review ? <p className="label-review" role="status">{t("Needs review · transcript changed")}</p> : null}
      <SourceList sources={entry.sources} timezone={timezone} showDate onCorrect={result.refresh} onDeleted={result.refresh} />
      <div className="button-row">
        {entry.fact?.status !== "superseded" ? <button className="text-button" disabled={mutation.busy}
          onClick={() => onEdit(entry, result.data!.revision)}>{t("Review and replace fact")}</button> : null}
        {entry.fact?.status === "confirmed" ? <button className="text-button" disabled={mutation.busy}
          onClick={() => void mutation.mutate({ action: "fact.reject", id: entry.id, expected_revision: result.data!.revision })}>
          {t("Reject fact")}</button> : null}
        <button className="text-button danger" disabled={mutation.busy} onClick={() => {
          if (window.confirm(t("Forget this fact and its entire replacement history? Recordings are kept.")))
            void mutation.mutate({ action: "note.delete", id: entry.id, expected_revision: result.data!.revision });
        }}>{t("Forget fact history")}</button>
      </div>
    </article>)}
    {result.data && entries.length < result.data.total ? <button className="button" disabled={loadingMore}
      onClick={() => void more()}>{t("Load more")}</button> : null}
    {result.error || mutation.error || pageError ? <ErrorState message={result.error || mutation.error || pageError} retry={result.refresh} /> : null}
  </>;
}

export function LabelFacts({ entityId, revision, timezone, onSaved }: {
  entityId: string; revision: string; timezone: string; onSaved: () => void;
}) {
  const [editor, setEditor] = useState<{ entry?: LabelEntry; revision: string }>();
  const [history, setHistory] = useState(false);
  return <section aria-label={t("Persistent facts")} className="label-panel">
    <h3>{t("Persistent facts")}</h3>
    <p className="caption">{t("Reviewed facts are your interpretation, kept separately from original transcript evidence. Source changes require another review.")}</p>
    <button className="button" onClick={() => setEditor({ revision })}>{t("Add a reviewed fact")}</button>
    {editor ? <NoteEditor key={editor.entry?.id || "new-fact"} fact entityId={entityId}
      revision={editor.revision} entry={editor.entry} timezone={timezone} onCancel={() => setEditor(undefined)}
      onSaved={() => { setEditor(undefined); onSaved(); }} /> : null}
    <FactList entityId={entityId} timezone={timezone} history={false} onSaved={onSaved}
      onEdit={(entry, revision) => setEditor({ entry, revision })} />
    <details onToggle={(event) => setHistory(event.currentTarget.open)}>
      <summary>{t("Fact history")}</summary>
      {history ? <FactList entityId={entityId} timezone={timezone} history onSaved={onSaved}
        onEdit={(entry, revision) => setEditor({ entry, revision })} /> : null}
    </details>
  </section>;
}
