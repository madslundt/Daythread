import { useEffect, useRef, useState } from "react";
import { ApiError, audioUrl, errorMessage, newId, request } from "./api";
import { dateTimeLabel } from "./format";
import { useResource } from "./useResource";
import { ErrorState, Loading, Notice } from "./UI";
import {
  deadlineLabel,
  type Deadline,
  type InboxFields,
  type InboxItem,
  type InboxKind,
  type InboxPage,
  type InboxStatus,
} from "./inboxTypes";
export function InboxEditor({
  id,
  timezone,
  onClose,
}: {
  id: string;
  timezone: string;
  onClose: () => void;
}) {
  const resource = useResource<InboxItem>(`/v1/inbox/${id}`, 15_000);
  const [base, setBase] = useState<InboxItem>();
  const [draft, setDraft] = useState<InboxFields>();
  const [status, setStatus] = useState<InboxStatus>("pending");
  const [duplicate, setDuplicate] = useState("");
  const [reset, setReset] = useState<(keyof InboxFields)[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const operation = useRef<{ fingerprint: string; id: string } | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const duplicates = useResource<InboxPage>("/v1/inbox?limit=200");
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(() => {
    if (resource.data && !base) {
      setBase(resource.data);
      setDraft(resource.data);
      setStatus(resource.data.status);
      setDuplicate(resource.data.duplicate_of || "");
    }
  }, [resource.data, base]);
  const conflict =
    base && resource.data && base.version !== resource.data.version;
  function change<K extends keyof InboxFields>(key: K, value: InboxFields[K]) {
    setDraft((d) => (d ? { ...d, [key]: value } : d));
    setReset((r) => r.filter((f) => f !== key));
    setSaved("");
  }
  function accept(item: InboxItem) {
    setBase(item);
    setDraft(item);
    setStatus(item.status);
    setDuplicate(item.duplicate_of || "");
    setReset([]);
    resource.setData(item);
    setSaved("Saved to your local backend.");
    operation.current = null;
    window.dispatchEvent(new Event("omi:archive-changed"));
  }
  async function save() {
    if (!draft || !base || busy) return;
    setBusy(true);
    setError("");
    setSaved("");
    const changes = Object.fromEntries(
      (Object.keys(base.proposal) as (keyof InboxFields)[])
        .filter(
          (k) =>
            !reset.includes(k) &&
            JSON.stringify(draft[k]) !== JSON.stringify(base[k]),
        )
        .map((k) => [k, draft[k]]),
    );
    const content = {
      expected_version: base.version,
      changes,
      status,
      reset,
      duplicate_of: status === "dismissed" ? duplicate || null : null,
    };
    const fingerprint = JSON.stringify(content);
    if (operation.current?.fingerprint !== fingerprint)
      operation.current = { fingerprint, id: newId() };
    try {
      const item = await request<InboxItem>(`/v1/inbox/${id}`, {
        method: "PATCH",
        body: JSON.stringify({
          ...content,
          operation_id: operation.current.id,
        }),
      });
      accept(item);
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.status === 0 &&
        operation.current
      ) {
        try {
          const latest = await request<InboxItem>(`/v1/inbox/${id}`);
          if (
            latest.history?.some((event) => event.id === operation.current?.id)
          ) {
            accept(latest);
            return;
          }
        } catch {
          /* Keep the draft and operation ID while disconnected. */
        }
      }
      setError(
        error instanceof ApiError && error.status === 409
          ? "The saved item changed. Your draft is preserved. Load the latest saved version below, compare it, then apply your draft."
          : errorMessage(error),
      );
      resource.refresh();
    } finally {
      setBusy(false);
    }
  }
  function updateDeadline(value: Partial<Deadline>) {
    change("deadline", {
      precision: "unknown",
      timezone,
      ...draft?.deadline,
      ...value,
      basis: "user edit",
    });
  }
  return (
    <dialog
      ref={dialog}
      className="inbox-dialog"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="section-heading">
        <h2>Review inbox item</h2>
        <button className="button" disabled={busy} onClick={onClose}>
          Close
        </button>
      </div>
      <p className="caption">
        Unsaved edits stay here after a failed request. Closing discards them.
      </p>
      {error || resource.error ? (
        <ErrorState
          message={error || resource.error}
          retry={resource.refresh}
        />
      ) : null}
      {saved ? <Notice>{saved}</Notice> : null}
      {draft && base ? (
        <form
          className="inbox-editor"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <fieldset className="inbox-fields" disabled={busy}>
            {conflict ? (
              <Notice>
                Latest saved: {resource.data!.title} ·{" "}
                {resource.data!.owner || "Owner unknown"} ·{" "}
                {deadlineLabel(resource.data!.deadline)} ·{" "}
                {resource.data!.status}.
                <button
                  type="button"
                  className="text-button"
                  onClick={() => setBase(resource.data)}
                >
                  Use latest version as save base (keep draft)
                </button>
              </Notice>
            ) : null}
            <label>
              Kind
              <select
                value={draft.kind}
                onChange={(e) => change("kind", e.target.value as InboxKind)}
              >
                <option value="commitment">Commitment</option>
                <option value="deadline">Deadline</option>
                <option value="question">Question</option>
              </select>
            </label>
            <label>
              Title
              <textarea
                required
                maxLength={500}
                value={draft.title}
                onChange={(e) => change("title", e.target.value)}
              />
            </label>
            <label>
              Owner
              <input
                maxLength={150}
                placeholder="Unknown"
                value={draft.owner || ""}
                onChange={(e) => change("owner", e.target.value || null)}
              />
            </label>
            <label>
              Deadline precision
              <select
                value={draft.deadline?.precision || "unknown"}
                onChange={(e) =>
                  updateDeadline({
                    precision: e.target.value as Deadline["precision"],
                    day: null,
                    instant: null,
                  })
                }
              >
                <option value="unknown">Unknown / unresolved</option>
                <option value="date">Date only</option>
                <option value="datetime">Exact instant</option>
              </select>
            </label>
            {draft.deadline?.precision === "date" ? (
              <label>
                Due date
                <input
                  type="date"
                  required
                  value={draft.deadline.day || ""}
                  onChange={(e) => updateDeadline({ day: e.target.value })}
                />
              </label>
            ) : null}
            {draft.deadline?.precision === "datetime" ? (
              <label>
                Due instant with UTC offset
                <input
                  required
                  placeholder="2026-10-20T14:00:00+02:00"
                  value={draft.deadline.instant || ""}
                  onChange={(e) => updateDeadline({ instant: e.target.value })}
                />
              </label>
            ) : null}
            <label>
              Deadline timezone
              <input
                required
                value={draft.deadline?.timezone || timezone}
                onChange={(e) => updateDeadline({ timezone: e.target.value })}
              />
            </label>
            <label>
              Status
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value as InboxStatus)}
              >
                <option value="pending">Pending</option>
                <option value="completed">
                  {draft.kind === "question" ? "Answered" : "Completed"}
                </option>
                <option value="dismissed">Dismissed</option>
              </select>
            </label>
            {status === "dismissed" ? (
              <label>
                Duplicate of (optional)
                <select
                  value={duplicate}
                  onChange={(e) => setDuplicate(e.target.value)}
                >
                  <option value="">Ordinary dismissal</option>
                  {duplicates.data?.items
                    .filter((i) => i.id !== id && !i.duplicate_of)
                    .map((i) => (
                      <option key={i.id} value={i.id}>
                        {i.title}
                      </option>
                    ))}
                </select>
              </label>
            ) : null}
            <details>
              <summary>Extracted values and reset overrides</summary>
              {(Object.keys(base.proposal) as (keyof InboxFields)[]).map(
                (key) => (
                  <div key={key}>
                    <p className="caption">
                      {key}:{" "}
                      {key === "deadline"
                        ? deadlineLabel(base.proposal.deadline)
                        : String(base.proposal[key] ?? "Unknown")}
                    </p>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => {
                        setDraft((d) =>
                          d ? { ...d, [key]: base.proposal[key] } : d,
                        );
                        setReset((r) => [...new Set([...r, key])]);
                      }}
                    >
                      {reset.includes(key)
                        ? "Reset on save"
                        : `Use extracted ${key}`}
                    </button>
                  </div>
                ),
              )}
            </details>
            <button
              className="button primary"
              disabled={busy || Boolean(conflict) || Boolean(resource.error)}
            >
              {busy ? "Saving…" : "Save review"}
            </button>
            <p className="caption">
              {base.health === "current"
                ? "Supporting transcript evidence"
                : "Evidence changed or was withdrawn. Prior excerpts are historical; review before relying on this item."}
            </p>
            {draft.kind === "question" ? (
              <p className="caption">
                No answer found in the scanned context; other recordings may
                contain one.
              </p>
            ) : null}
            {resource.data?.evidence?.map((span, index) => (
              <blockquote key={index}>
                <p>{span.quote}</p>
                <p className="caption">
                  {dateTimeLabel(span.started_at, timezone)} ·{" "}
                  {span.current ? "Current transcript" : "Prior transcript"} ·
                  chunk-level timing · {span.source_id}
                </p>
                {span.audio_available ? (
                  <audio
                    controls
                    preload="none"
                    src={audioUrl(span.source_id)}
                  />
                ) : (
                  <p className="caption">Original audio unavailable</p>
                )}
              </blockquote>
            ))}
            <details>
              <summary>Change history</summary>
              {resource.data?.history?.map((event) => (
                <p className="caption" key={event.id}>
                  {dateTimeLabel(event.created_at, timezone)} · {event.kind}
                </p>
              ))}
            </details>
          </fieldset>
        </form>
      ) : (
        <Loading />
      )}
    </dialog>
  );
}
