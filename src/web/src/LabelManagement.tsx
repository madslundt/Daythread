import { navigatePath } from "./routing";
import { useState } from "react";
import { useResource } from "./useResource";
import { errorMessage, newId, request } from "./api";
import { EntityFields, cleanDraft, emptyDraft } from "./LabelEditors";
import {
  meanings,
  useLabelMutation,
  type Directory,
  type Entity,
  type EntityDraft,
  type EntityTimeline,
  type LabelEntry,
} from "./labelModel";
import { ErrorState } from "./UI";
export function EntityManagement({
  entity,
  revision,
  entries,
  complete,
  onSaved,
}: {
  entity: Entity;
  revision: string;
  entries: LabelEntry[];
  complete: boolean;
  onSaved: () => void;
}) {
  const [refreshError, setRefreshError] = useState("");
  const [editRevision, setEditRevision] = useState(revision);
  const [draft, setDraft] = useState<EntityDraft>(entity);
  const [splitDraft, setSplitDraft] = useState<EntityDraft>({
    ...emptyDraft,
    kind: entity.kind,
  });
  const [selected, setSelected] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [splitId] = useState(newId);
  const directory = useResource<Directory>("/v1/labels");
  const preview = useResource<EntityTimeline>(
    target ? `/v1/labels/${target}/timeline?limit=200` : null,
  );
  const mutation = useLabelMutation((action) => {
    if (action === "entity.delete") navigatePath("/labels");
    else onSaved();
  });
  async function refreshVersion() {
    try {
      const current = await request<Directory>("/v1/labels");
      setEditRevision(current.revision);
      setRefreshError("");
    } catch (error) {
      setRefreshError(errorMessage(error));
    }
  }
  return (
    <div className="label-panel label-fields">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void mutation.mutate({
            action: "entity.save",
            id: entity.id,
            expected_revision: editRevision,
            value: cleanDraft(draft),
          });
        }}
      >
        <h3>Edit name, description and aliases</h3>
        <EntityFields value={draft} onChange={setDraft} fixedKind />
        <button className="button" disabled={mutation.busy}>
          Save changes
        </button>
      </form>
      <details>
        <summary>Merge into another {entity.kind}</summary>
        <p>
          Moves all associations and note assignments. The old name becomes an
          alias. Different association meanings stay distinct.
        </p>
        <label>
          Destination
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Choose a label</option>
            {directory.data?.items
              .filter((e) => e.id !== entity.id && e.kind === entity.kind)
              .map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name} · {e.description}
                </option>
              ))}
          </select>
        </label>
        {preview.data ? (
          <>
            <p>
              Keep {preview.data.entity.name} ·{" "}
              {preview.data.entity.description}. Combine {preview.data.total}{" "}
              destination entries with all entries from {entity.name}.
            </p>
            <p>
              Destination aliases:{" "}
              {preview.data.entity.aliases.join(", ") || "None"}
            </p>
            <button
              className="button"
              disabled={mutation.busy}
              onClick={() => {
                if (
                  window.confirm(
                    `Merge ${entity.name} into ${preview.data!.entity.name}? Review names and associations before confirming.`,
                  )
                )
                  void mutation.mutate({
                    action: "entity.merge",
                    id: entity.id,
                    target_id: target,
                    expected_revision: editRevision,
                  });
              }}
            >
              Confirm merge
            </button>
          </>
        ) : null}
      </details>
      <details>
        <summary>Split selected assignments into a new label</summary>
        <p>
          Only checked associations and note assignments move. Set aliases for
          the new label explicitly; existing aliases stay here.
        </p>
        <EntityFields value={splitDraft} onChange={setSplitDraft} fixedKind />
        {entries.map((entry) => (
          <label className="label-choice" key={entry.id}>
            <input
              type="checkbox"
              checked={selected.includes(entry.id)}
              onChange={(e) =>
                setSelected((old) =>
                  e.target.checked
                    ? [...old, entry.id]
                    : old.filter((id) => id !== entry.id),
                )
              }
            />
            <span>
              {entry.title || meanings[entry.relation || ""]} ·{" "}
              {entry.started_at}
            </span>
          </label>
        ))}
        {!complete ? (
          <p>
            Clear date filters and load all entries to review the full label
            before splitting.
          </p>
        ) : null}
        <button
          className="button"
          disabled={
            mutation.busy || !selected.length || !splitDraft.name.trim()
          }
          onClick={() =>
            void mutation.mutate({
              action: "entity.split",
              id: entity.id,
              expected_revision: editRevision,
              new_id: splitId,
              value: cleanDraft(splitDraft),
              link_ids: entries
                .filter(
                  (e) => e.entry_type === "source" && selected.includes(e.id),
                )
                .map((e) => e.id),
              decision_link_ids: entries
                .filter(
                  (e) => e.entry_type === "decision" && selected.includes(e.id),
                )
                .map((e) => e.id),
              note_ids: entries
                .filter(
                  (e) => e.entry_type === "note" && selected.includes(e.id),
                )
                .map((e) => e.id),
            })
          }
        >
          Move selected assignments
        </button>
      </details>
      <button
        className="text-button danger"
        disabled={mutation.busy}
        onClick={() => {
          if (
            window.confirm(
              "Delete this label and its associations and attached annotations? Recordings remain, including any names in their transcripts.",
            )
          )
            void mutation.mutate({
              action: "entity.delete",
              id: entity.id,
              expected_revision: editRevision,
            });
        }}
      >
        Delete label
      </button>
      {refreshError || mutation.error ? (
        <ErrorState message={refreshError || mutation.error} />
      ) : null}
      <button className="text-button" onClick={() => void refreshVersion()}>
        Reload current metadata version for review (keep draft)
      </button>
    </div>
  );
}
