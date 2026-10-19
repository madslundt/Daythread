import { statusLabel } from "./statusLabels";
import { t, source } from "./i18n";
import { useEffect, useRef, useState } from "react";
import { ActionFollowups } from "./ActionFollowups";
import { actionableKind } from "./dayHighlights";
import { ApiError, audioUrl, errorMessage, newId, request } from "./api";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";
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
  const unsaved = Boolean(base && draft && (status !== base.status || duplicate !== (base.duplicate_of || "") || reset.length ||
    ([...Object.keys(base.proposal), "user_note"] as (keyof InboxFields)[]).some((field) => JSON.stringify(draft[field]) !== JSON.stringify(base[field]))));
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
    setSaved(t("Saved to your local backend."));
    operation.current = null;
    window.dispatchEvent(new Event("omi:inbox-changed"));
  }
  async function save() {
    if (!draft || !base || busy) return;
    setBusy(true);
    setError("");
    setSaved("");
    const changes = Object.fromEntries(
      ([...Object.keys(base.proposal), "user_note"] as (keyof InboxFields)[])
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
          ? t("The saved item changed. Your draft is preserved. Load the latest saved version below, compare it, then apply your draft.")
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
      basis: source("user edit"),
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
        <h2>{t("Review inbox item")}</h2>
        <button className="button" disabled={busy} onClick={onClose}>
          {t("Close")}{" "}</button>
      </div>
      <p className="caption">
        {t("Unsaved edits stay here after a failed request. Closing discards them.")}{" "}</p>
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
                {t("Latest saved:")}{" "}{resource.data!.title} ·{" "}
                {resource.data!.owner || t("Owner unknown")} ·{" "}
                {deadlineLabel(resource.data!.deadline)} ·{" "}
                {statusLabel(resource.data!.status)}.
                <button
                  type="button"
                  className="text-button"
                  onClick={() => setBase(resource.data)}
                >
                  {t("Use latest version as save base (keep draft)")}{" "}</button>
              </Notice>
            ) : null}
            <label>
              {t("Kind")}{" "}<select
                value={draft.kind}
                onChange={(e) => change("kind", e.target.value as InboxKind)}
              >
                <option value="commitment">{t("Commitment")}</option>
                <option value="deadline">{t("Deadline")}</option>
                <option value="question">{t("Question")}</option>
                <option value="reminder">{statusLabel("reminder")}</option>
                <option value="event">{statusLabel("event")}</option>
                <option value="reflection">{statusLabel("reflection")}</option>
                <option value="note">{statusLabel("note")}</option>
              </select>
            </label>
            <label>
              {t("Title")}{" "}<textarea
                required
                maxLength={500}
                value={draft.title}
                onChange={(e) => change("title", e.target.value)}
              />
            </label>
            {actionableKind(draft.kind) ? <><label>
              {t("Owner")}{" "}<input
                maxLength={150}
                placeholder={t("Unknown")}
                value={draft.owner || ""}
                onChange={(e) => change("owner", e.target.value || null)}
              />
            </label><label>
              {t("Deadline precision")}{" "}<select
                value={draft.deadline?.precision || "unknown"}
                onChange={(e) =>
                  updateDeadline({
                    precision: e.target.value as Deadline["precision"],
                    day: null,
                    instant: null,
                  })
                }
              >
                <option value="unknown">{t("Unknown / unresolved")}</option>
                <option value="date">{t("Date only")}</option>
                <option value="datetime">{t("Exact instant")}</option>
              </select>
            </label></> : null}
            <label>
              {draft.kind === "question" ? t("Your answer") : t("Your notes")}
              <textarea rows={4} maxLength={8000} value={draft.user_note || ""} onChange={(event) => change("user_note", event.target.value || null)} />
            </label>
            <p className="caption">{t("Your own words. Saving notes does not change the recording or automatically close this item.")}</p>
            {base.user_note_provenance ? <button type="button" className="text-button" onClick={() => {
              setDraft((value) => value ? { ...value, user_note: null } : value);
              setReset((value) => [...new Set([...value, "user_note" as const])]);
            }}>{t("Clear personal note on save")}</button> : null}
            {actionableKind(draft.kind) && draft.deadline?.precision === "date" ? (
              <label>
                {t("Due date")}{" "}<input
                  type="date"
                  required
                  value={draft.deadline.day || ""}
                  onChange={(e) => updateDeadline({ day: e.target.value })}
                />
              </label>
            ) : null}
            {actionableKind(draft.kind) && draft.deadline?.precision === "datetime" ? (
              <label>
                {t("Due instant with UTC offset")}{" "}<input
                  required
                  placeholder={t("2026-10-20T14:00:00+02:00")}
                  value={draft.deadline.instant || ""}
                  onChange={(e) => updateDeadline({ instant: e.target.value })}
                />
              </label>
            ) : null}
            {actionableKind(draft.kind) ? <label>
              {t("Deadline timezone")}{" "}<input
                required
                value={draft.deadline?.timezone || timezone}
                onChange={(e) => updateDeadline({ timezone: e.target.value })}
              />
            </label> : null}
            <label>
              {t("Status")}{" "}<select
                value={status}
                onChange={(e) => setStatus(e.target.value as InboxStatus)}
              >
                <option value="pending">{t("Pending")}</option>
                <option value="completed">
                  {draft.kind === "question" ? t("Answered") : !actionableKind(draft.kind) ? t("Reviewed") : t("Completed")}
                </option>
                <option value="dismissed">{t("Dismissed")}</option>
              </select>
            </label>
            {status === "dismissed" ? (
              <label>
                {t("Duplicate of (optional)")}{" "}<select
                  value={duplicate}
                  onChange={(e) => setDuplicate(e.target.value)}
                >
                  <option value="">{t("Ordinary dismissal")}</option>
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
              <summary>{t("Extracted values and reset overrides")}</summary>
              {(Object.keys(base.proposal) as (keyof InboxFields)[]).map(
                (key) => (
                  <div key={key}>
                    <p className="caption">
                      {key}:{" "}
                      {key === "deadline"
                        ? deadlineLabel(base.proposal.deadline)
                        : String(base.proposal[key] ?? t("Unknown"))}
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
                        ? t("Reset on save")
                        : t("Use extracted {value0}", { value0: key })}
                    </button>
                  </div>
                ),
              )}
            </details>
            <button
              className="button primary"
              disabled={busy || Boolean(conflict) || Boolean(resource.error)}
            >
              {busy ? t("Saving…") : t("Save review")}
            </button>
            <p className="caption">
              {base.health === "current"
                ? t("Supporting transcript evidence")
                : t("Evidence changed or was withdrawn. Prior excerpts are historical; review before relying on this item.")}
            </p>
            {draft.kind === "question" ? (
              <p className="caption">
                {t("No answer found in the scanned context; other recordings may contain one.")}{" "}</p>
            ) : null}
            {resource.data?.evidence?.map((span, index) => (
              <blockquote key={index}>
                <p>{span.quote}</p>
                <p className="caption">
                  {dateTimeLabel(span.started_at, timezone)} ·{" "}
                  {span.current ? t("Current transcript") : t("Prior transcript")} {t("· chunk-level timing ·")}{" "}{span.source_id}
                </p>
                {span.audio_available ? (
                  <RecordingAudioPlayer
                    src={audioUrl(span.source_id)}
                  />
                ) : (
                  <p className="caption">{t("Original audio unavailable")}</p>
                )}
              </blockquote>
            ))}
            <details>
              <summary>{t("Change history")}</summary>
              {resource.data?.history?.map((event) => (
                <p className="caption" key={event.id}>
                  {dateTimeLabel(event.created_at, timezone)} · {statusLabel(event.kind)}
                </p>
              ))}
            </details>
          </fieldset>
        </form>
      ) : (
        <Loading />
      )}
      {resource.data && !conflict ? <ActionFollowups key={`${id}:${resource.data.version}`} item={resource.data} timezone={timezone} disabled={busy || unsaved} /> : null}
    </dialog>
  );
}
