import { statusLabel } from "./statusLabels";
import { t } from "./i18n";
import { navigatePath, subscribeNavigation } from "./routing";
import { ImportanceButton, ImportantBadge } from "./Importance";
import { LabelDecisionLink } from "./LabelDecisionLink";
import { DecisionDetail } from "./Decisions";
import { LabelRequestEpoch } from "./labelRequests";
import { EntityManagement } from "./LabelManagement";
import { useEffect, useRef, useState } from "react";
import { errorMessage, newId, request } from "./api";
import {
  EntityFields,
  NoteEditor,
  cleanDraft,
  emptyDraft,
} from "./LabelEditors";
import {
  kinds,
  meanings,
  sourceRef,
  useLabelMutation,
  type Directory,
  type EntityDraft,
  type EntityTimeline,
  type LabelEntry,
} from "./labelModel";
import { SourceList } from "./SourceList";
import { useResource } from "./useResource";
import { ErrorState } from "./UI";

function selectedId() {
  return window.location.pathname.split("/")[2] || "";
}

export function Labels({ timezone }: { timezone: string }) {
  const [id, setId] = useState(selectedId);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<EntityDraft>(emptyDraft);
  const [createId, setCreateId] = useState(newId);
  const directory = useResource<Directory>(
    `/v1/labels?q=${encodeURIComponent(query)}${kind ? `&kind=${kind}` : ""}`,
  );
  const mutation = useLabelMutation(() => {
    setCreating(false);
    directory.refresh();
    navigatePath(`/labels/${createId}`);
    setCreateId(newId());
    setDraft(emptyDraft);
  });
  useEffect(() => {
    const sync = () => setId(selectedId());
    return subscribeNavigation(sync);
  }, []);
  useEffect(() => {
    const sync = () => directory.refresh();
    window.addEventListener("omi:archive-changed", sync);
    return () => window.removeEventListener("omi:archive-changed", sync);
  }, [directory.refresh]);
  if (id) return <EntityDetail key={id} id={id} timezone={timezone} />;
  return (
    <section className="labels-page">
      <div className="label-fields">
        <label>
          {t("Search names and aliases")}{" "}<input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label>
          {t("Kind")}{" "}<select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">{t("People and projects")}</option>
            <option value="person">{t("People")}</option>
            <option value="project">{t("Projects")}</option>
          </select>
        </label>
      </div>
      <button className="button" onClick={() => setCreating(!creating)}>
        {creating ? t("Close new label") : t("New person or project")}
      </button>
      {creating ? (
        <form
          className="label-panel"
          onSubmit={(e) => {
            e.preventDefault();
            void mutation.mutate({
              action: "entity.save",
              id: createId,
              create: true,
              expected_revision: directory.data?.revision,
              value: cleanDraft(draft),
            });
          }}
        >
          <p className="caption">
            {t("Keep people and projects distinct with names, aliases and descriptions. All associations and annotations are chosen by you.")}
          </p>
          <EntityFields value={draft} onChange={setDraft} />
          <button
            className="button dark"
            disabled={mutation.busy || !directory.data}
          >
            {t("Save label")}{" "}</button>
          {mutation.error ? <ErrorState message={mutation.error} /> : null}
        </form>
      ) : null}
      {directory.error ? (
        <ErrorState message={directory.error} retry={directory.refresh} />
      ) : null}
      {directory.loading ? (
        <p role="status">{t("Loading labels…")}</p>
      ) : !directory.data?.items.length ? (
        <p>
          {t("No matching labels. Create one, then link recordings from their source details.")}{" "}</p>
      ) : null}
      <div className="label-directory">
        {directory.data?.items.map((entity) => (
          <a
            className="label-panel"
            key={entity.id}
            href={`/labels/${entity.id}`}
          >
            <span className="caption">
              {entity.kind === "person" ? t("Person") : t("Project")}
            </span>
            <h2>{entity.name} <ImportantBadge target={{ kind: "label", id: entity.id }} /></h2>
            <p>{entity.description}</p>
            <p className="caption">{entity.aliases.join(" · ")}</p>
          </a>
        ))}
      </div>
    </section>
  );
}

function EntityDetail({ id, timezone }: { id: string; timezone: string }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const query = new URLSearchParams({
    limit: "50",
    ...(start ? { start_day: start } : {}),
    ...(end ? { end_day: end } : {}),
  });
  const epoch = useRef(new LabelRequestEpoch());
  const snapshot = `${id}|${query.toString()}`;
  const snapshotRef = useRef(snapshot);
  if (snapshotRef.current !== snapshot) {
    snapshotRef.current = snapshot;
    epoch.current.replace();
  }
  const timeline = useResource<EntityTimeline>(
    `/v1/labels/${id}/timeline?${query}`,
  );
  const displayedRevision = useRef(timeline.data?.revision);
  if (displayedRevision.current !== timeline.data?.revision) {
    displayedRevision.current = timeline.data?.revision;
    epoch.current.replace();
  }
  const [extra, setExtra] = useState<LabelEntry[]>([]);
  const [pageError, setPageError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [manage, setManage] = useState(false);
  const [decisionId, setDecisionId] = useState("");
  const [editor, setEditor] = useState<LabelEntry | "new">();
  useEffect(() => {
    epoch.current.replace();
    setExtra([]);
    setLoadingMore(false);
    return () => {
      epoch.current.replace();
    };
  }, [timeline.data?.revision, start, end]);
  useEffect(() => {
    const refresh = () => {
      epoch.current.replace();
      setExtra([]);
      setLoadingMore(false);
      timeline.refresh();
    };
    window.addEventListener("omi:archive-changed", refresh);
    window.addEventListener("omi:decisions-changed", refresh);
    return () => {
      window.removeEventListener("omi:archive-changed", refresh);
      window.removeEventListener("omi:decisions-changed", refresh);
    };
  }, [timeline.refresh]);
  const data = timeline.data;
  const entries = data ? [...data.items, ...extra] : [];
  const refresh = () => {
    epoch.current.replace();
    setExtra([]);
    setLoadingMore(false);
    timeline.refresh();
  };
  const mutation = useLabelMutation(refresh);
  async function loadMore() {
    if (!data) return;
    const token = epoch.current.capture();
    const requestedSnapshot = snapshot;
    setLoadingMore(true);
    setPageError("");
    try {
      const next = await request<EntityTimeline>(
        `/v1/labels/${id}/timeline?${query}&offset=${entries.length}&revision=${data.revision}`,
      );
      if (
        epoch.current.accepts(token) &&
        snapshotRef.current === requestedSnapshot &&
        next.revision === data.revision
      )
        setExtra((old) => [...old, ...next.items]);
    } catch (reason) {
      if (epoch.current.accepts(token)) setPageError(errorMessage(reason));
    } finally {
      if (epoch.current.accepts(token)) setLoadingMore(false);
    }
  }
  return (
    <section className="labels-page">
      <a className="text-link" href="/labels">
        {t("← People & projects")}{" "}</a>
      {timeline.error ? (
        <ErrorState message={timeline.error} retry={refresh} />
      ) : null}
      {data ? (
        <>
          <div className="section-heading">
            <div>
              <p className="caption">{statusLabel(data.entity.kind)}</p>
              <h2>{data.entity.name}</h2>
              <ImportanceButton target={{ kind: "label", id: data.entity.id }} label={data.entity.name} />
              <p>{data.entity.description}</p>
              <p>{data.entity.aliases.join(" · ")}</p>
            </div>
            <button className="button" onClick={() => setManage(!manage)}>
              {t("Manage label")}{" "}</button>
          </div>
          {manage ? (
            <EntityManagement
              entity={data.entity}
              revision={data.revision}
              entries={entries}
              complete={entries.length === data.total}
              onSaved={() => {
                setManage(false);
                refresh();
              }}
            />
          ) : null}
          <div className="label-filters">
            <label>
              {t("From")}{" "}<input
                type="date"
                value={start}
                onChange={(e) => {
                  setManage(false);
                  setStart(e.target.value);
                }}
              />
            </label>
            <label>
              {t("Through")}{" "}<input
                type="date"
                value={end}
                onChange={(e) => {
                  setManage(false);
                  setEnd(e.target.value);
                }}
              />
            </label>
            <button
              className="text-button"
              onClick={() => {
                setStart("");
                setEnd("");
              }}
            >
              {t("All dates")}{" "}</button>
          </div>
          <p className="caption">
            {t("Dates in")}{" "}{timezone}{t(". Recordings keep source-level precision. Groups and notes are written by you; speaker attributions from imports remain unverified.")}{" "}</p>
          <button className="button" onClick={() => setEditor("new")}>
            {t("Group conversations / add decision or question")}{" "}</button>
          <LabelDecisionLink
            entityId={data.entity.id}
            revision={data.revision}
            timezone={timezone}
            onSaved={refresh}
          />
          {decisionId ? (
            <DecisionDetail
              id={decisionId}
              timezone={timezone}
              onClose={() => setDecisionId("")}
              onNavigate={setDecisionId}
            />
          ) : null}
          {editor ? (
            <NoteEditor
              key={editor === "new" ? "new" : editor.id}
              entityId={data.entity.id}
              revision={data.revision}
              timezone={timezone}
              entry={editor === "new" ? undefined : editor}
              onSaved={() => {
                setEditor(undefined);
                refresh();
              }}
              onCancel={() => setEditor(undefined)}
            />
          ) : null}
          {!entries.length ? (
            <p>
              {t("No linked evidence in these dates. Add labels from source details or select recordings in a new annotation.")}{" "}</p>
          ) : null}
          {entries.map((entry) => (
            <article className="label-panel" key={entry.id}>
              {entry.entry_type === "decision" ? (
                <>
                  <p className="caption">
                    {t("Decision register ·")}{" "}{statusLabel(entry.decision?.review_status)} {t("· evidence")}{" "}{statusLabel(entry.decision?.evidence_condition)}
                  </p>
                  <h3>{entry.title}</h3>
                  <button
                    className="text-button"
                    onClick={() => setDecisionId(entry.decision_id || "")}
                  >
                    {t("Open register record and exact evidence")}{" "}</button>
                </>
              ) : entry.entry_type === "note" ? (
                <>
                  <p className="caption">{t(kinds[entry.kind || ""] || "")}</p>
                  <h3>{entry.title}</h3>
                  <p className="transcript-text">{entry.body}</p>
                </>
              ) : (
                <p>{t(meanings[entry.relation || ""] || "")}</p>
              )}
              {entry.needs_review ? (
                <p className="label-review" role="status">
                  {t("Needs review · transcript evidence changed. This association or annotation is not confirmed against the current source.")}{" "}</p>
              ) : null}
              <SourceList
                sources={entry.sources}
                timezone={timezone}
                showDate
                onCorrect={refresh}
                onDeleted={refresh}
              />
              <div className="button-row">
                {entry.entry_type === "note" ? (
                  <button
                    className="text-button"
                    onClick={() => setEditor(entry)}
                  >
                    {t("Edit / review annotation")}{" "}</button>
                ) : entry.needs_review ? (
                  <button
                    className="text-button"
                    disabled={mutation.busy}
                    onClick={() =>
                      void mutation.mutate({
                        action:
                          entry.entry_type === "decision"
                            ? "decision_link.save"
                            : "link.save",
                        ...(entry.entry_type === "decision"
                          ? {
                              decision_id: entry.decision_id,
                              decision_version: entry.decision?.version,
                            }
                          : {
                              source: sourceRef(entry.sources[0]),
                              relation: entry.relation,
                            }),
                        id: entry.id,
                        entity_id: data.entity.id,
                        expected_revision: data.revision,
                      })
                    }
                  >
                    {t("Confirm association after review")}{" "}</button>
                ) : null}
                <button
                  className="text-button danger"
                  disabled={mutation.busy}
                  onClick={() => {
                    if (
                      window.confirm(
                        entry.entry_type === "note"
                          ? t("Delete this annotation? Recordings are kept.")
                          : t("Detach this association? Recordings are kept."),
                      )
                    )
                      void mutation.mutate({
                        action:
                          entry.entry_type === "decision"
                            ? "decision_link.delete"
                            : entry.entry_type === "note"
                              ? "note.delete"
                              : "link.delete",
                        id: entry.id,
                        expected_revision: data.revision,
                      });
                  }}
                >
                  {entry.entry_type === "note"
                    ? t("Delete annotation")
                    : t("Detach association")}
                </button>
              </div>
            </article>
          ))}
          {entries.length < data.total ? (
            <button
              className="button"
              disabled={loadingMore}
              onClick={() => void loadMore()}
            >
              {t("Load more (")}{entries.length} {t("of")}{" "}{data.total})
            </button>
          ) : null}
          {pageError || mutation.error ? (
            <ErrorState message={pageError || mutation.error} retry={refresh} />
          ) : null}
        </>
      ) : timeline.loading ? (
        <p role="status">{t("Loading timeline…")}</p>
      ) : null}
    </section>
  );
}
