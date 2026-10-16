import { useEffect, useState } from "react";
import { ApiError, errorMessage, newId, request } from "./api";
import { dateTimeLabel } from "./format";
import {
  kinds,
  sourceRef,
  useLabelMutation,
  type EntityDraft,
  type LabelEntry,
} from "./labelModel";
import type { Source } from "./types";
import { useResource } from "./useResource";
import { ErrorState } from "./UI";

export function EntityFields({
  value,
  onChange,
  fixedKind = false,
}: {
  value: EntityDraft;
  onChange: (value: EntityDraft) => void;
  fixedKind?: boolean;
}) {
  return (
    <div className="label-fields">
      <label>
        Kind
        <select
          value={value.kind}
          disabled={fixedKind}
          onChange={(e) =>
            onChange({ ...value, kind: e.target.value as EntityDraft["kind"] })
          }
        >
          <option value="person">Person</option>
          <option value="project">Project</option>
        </select>
      </label>
      <label>
        Name
        <input
          required
          maxLength={120}
          value={value.name}
          onChange={(e) => onChange({ ...value, name: e.target.value })}
        />
      </label>
      <label>
        Distinguishing description
        <textarea
          maxLength={2000}
          value={value.description}
          onChange={(e) => onChange({ ...value, description: e.target.value })}
          placeholder="For example: Alex from the cycling club"
        />
      </label>
      <label>
        Aliases · one per line
        <textarea
          maxLength={3600}
          value={value.aliases.join("\n")}
          onChange={(e) =>
            onChange({ ...value, aliases: e.target.value.split("\n") })
          }
        />
      </label>
      <p className="caption">
        Names and aliases can match other labels. A name mention never confirms
        participation. On rename, add the previous name as an alias only if you
        want to keep it.
      </p>
    </div>
  );
}

export function cleanDraft(value: EntityDraft): EntityDraft {
  return {
    kind: value.kind,
    name: value.name.trim(),
    description: value.description.trim(),
    aliases: value.aliases.map((s) => s.trim()).filter(Boolean),
  };
}
export const emptyDraft: EntityDraft = {
  kind: "person",
  name: "",
  description: "",
  aliases: [],
};

export function NoteEditor({
  entityId,
  revision,
  entry,
  timezone,
  onSaved,
  onCancel,
}: {
  entityId: string;
  revision: string;
  entry?: LabelEntry;
  timezone: string;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [editRevision, setEditRevision] = useState(revision);
  const [refreshError, setRefreshError] = useState("");
  const [id] = useState(() => entry?.id || newId());
  const [title, setTitle] = useState(entry?.title || "");
  const [body, setBody] = useState(entry?.body || "");
  const [kind, setKind] = useState(entry?.kind || "conversation");
  const [sources, setSources] = useState<Source[]>(entry?.sources || []);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  useEffect(() => {
    setPage(0);
  }, [query]);
  const available = useResource<{ items: Source[]; total: number }>(
    `/v1/transcripts?limit=50&offset=${page * 50}&q=${encodeURIComponent(query)}`,
  );
  const mutation = useLabelMutation(onSaved);
  async function refreshEvidence() {
    setRefreshError("");
    try {
      const updated: Source[] = [];
      for (const source of sources) {
        try {
          const result = await request<{ source: Source }>(
            `/v1/labels/sources/${source.id}`,
          );
          if (result.source.text !== null) updated.push(result.source);
        } catch (error) {
          if (!(error instanceof ApiError && error.status === 404)) throw error;
        }
      }
      const current = await request<{ revision: string }>("/v1/labels");
      setSources(updated);
      setEditRevision(current.revision);
      available.refresh();
    } catch (error) {
      setRefreshError(errorMessage(error));
    }
  }
  function toggle(source: Source) {
    setSources((old) =>
      old.some((s) => s.id === source.id)
        ? old.filter((s) => s.id !== source.id)
        : [...old, source],
    );
  }
  return (
    <form
      className="label-panel label-fields"
      onSubmit={(e) => {
        e.preventDefault();
        void mutation.mutate({
          action: "note.save",
          create: !entry,
          id,
          expected_revision: editRevision,
          kind,
          title,
          body,
          entity_ids: entry?.entity_ids || [entityId],
          sources: sources.map(sourceRef),
        });
      }}
    >
      <h3>{entry ? "Edit and review annotation" : "Add a cited annotation"}</h3>
      <label>
        Type
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {Object.entries(kinds).map(([k, label]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Title
        <input
          required
          maxLength={120}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label>
        Your note
        <textarea
          maxLength={2000}
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
      </label>
      <p className="caption">
        Select the recordings that support your note. A group has only the
        boundaries you select. Open questions have no automatic resolution
        status.
      </p>
      <fieldset>
        <legend>Selected evidence · {sources.length}</legend>
        {sources.map((source) => (
          <label className="label-choice" key={source.id}>
            <input type="checkbox" checked onChange={() => toggle(source)} />
            <span>
              {dateTimeLabel(source.started_at, timezone)} ·{" "}
              {source.text || "No speech"}
            </span>
          </label>
        ))}
      </fieldset>
      <label>
        Find recordings
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      {available.error ? (
        <ErrorState message={available.error} retry={available.refresh} />
      ) : null}
      <div className="label-source-choices">
        {available.data?.items
          .filter(
            (s) =>
              s.text !== null &&
              !sources.some((selected) => selected.id === s.id),
          )
          .map((source) => (
            <label className="label-choice" key={source.id}>
              <input
                type="checkbox"
                checked={false}
                onChange={() => toggle(source)}
              />
              <span>
                {dateTimeLabel(source.started_at, timezone)} ·{" "}
                {source.text || "No speech"}
              </span>
            </label>
          ))}
      </div>
      <div className="button-row">
        <button
          type="button"
          className="button small"
          disabled={page === 0 || available.loading}
          onClick={() => setPage((p) => p - 1)}
        >
          Previous recordings
        </button>
        <span>
          Page {page + 1} · {available.data?.total ?? 0} matches
        </span>
        <button
          type="button"
          className="button small"
          disabled={
            available.loading ||
            !available.data ||
            (page + 1) * 50 >= available.data.total
          }
          onClick={() => setPage((p) => p + 1)}
        >
          Next recordings
        </button>
      </div>
      {entry?.needs_review ? (
        <p role="status">
          Evidence changed. Read the current sources before saving to confirm
          this annotation again.
        </p>
      ) : null}
      {mutation.error || refreshError ? (
        <ErrorState message={refreshError || mutation.error} />
      ) : null}
      <button
        type="button"
        className="text-button"
        disabled={mutation.busy}
        onClick={() => void refreshEvidence()}
      >
        Refresh selected evidence and version for review · keep draft
      </button>
      <div className="button-row">
        <button
          className="button dark"
          disabled={mutation.busy || !sources.length}
        >
          {mutation.busy ? "Saving…" : "Save reviewed annotation"}
        </button>
        <button
          type="button"
          className="button"
          onClick={onCancel}
          disabled={mutation.busy}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
