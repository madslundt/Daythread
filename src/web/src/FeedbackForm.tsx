import { statusLabel } from "./statusLabels";
import { displayLocale, t } from "./i18n";
import { useState } from "react";
import { errorMessage, newId, request } from "./api";
import { useResource } from "./useResource";
import type { Source } from "./types";
import { ErrorState } from "./UI";

const stages = ["unknown", "capture", "stt", "retrieval", "generation"];
export function FeedbackForm({
  sourceId,
  outputId,
  outputText,
  start,
  end,
}: {
  sourceId?: string;
  outputId?: string;
  outputText?: string;
  start?: string;
  end?: string;
}) {
  const [open, setOpen] = useState(false);
  const [issue, setIssue] = useState(
    start && !sourceId && !outputId ? "missing" : "incorrect",
  );
  const [stage, setStage] = useState("unknown");
  const [excerpt, setExcerpt] = useState("");
  const [note, setNote] = useState("");
  const [expected, setExpected] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const expectedIDs = expected.split(",").filter(Boolean);
  const recordings = useResource<{ items: Source[] }>(
    open && query
      ? `/v1/transcripts?${new URLSearchParams({ q: query, limit: "10" })}`
      : null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  // Keep one ID across ambiguous network failures. The user explicitly retries.
  const [id, setId] = useState(newId);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const receipt = await request<{ id: string }>(`/v1/feedback/${id}`, {
        method: "PUT",
        body: JSON.stringify({
          issue,
          stage,
          note,
          excerpt,
          source_id: sourceId,
          output_id: outputId,
          start,
          end,
          expected_source_ids: expected.split(/[\s,]+/).filter(Boolean),
        }),
      });
      if (receipt.id?.toLowerCase() !== id.toLowerCase())
        throw new Error(
          t("Backend receipt was not confirmed. Retry this report safely."),
        );
      setSaved(true);
      setOpen(false);
      window.dispatchEvent(new Event("omi:feedback-changed"));
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  if (saved)
    return (
      <p role="status" className="caption">
        {t("Feedback saved on the backend.")}{" "}
        <button
          className="text-button"
          onClick={() => {
            setSaved(false);
            setId(newId());
          }}
        >
          {t("Report another problem")}{" "}</button>
      </p>
    );
  return (
    <div className="feedback-form">
      <button className="text-button" onClick={() => setOpen(!open)}>
        {t("Report incorrect or missing information")}{" "}</button>
      {open ? (
        <form onSubmit={save}>
          <fieldset disabled={busy}>
            <label>
              {t("Problem")}{" "}
              <select
                aria-label={t("Problem")}
                value={issue}
                onChange={(e) => {
                  setIssue(e.target.value);
                  setId(newId());
                }}
              >
                <option value="incorrect">{t("Incorrect information")}</option>
                <option value="missing">{t("Missing information")}</option>
              </select>
            </label>
            <label>
              {t("Suspected stage")}{" "}
              <select
                aria-label={t("Suspected stage")}
                value={stage}
                onChange={(e) => {
                  setStage(e.target.value);
                  setId(newId());
                }}
              >
                {stages.map((s) => (
                  <option key={s} value={s}>
                    {s === "unknown"
                      ? t("I don’t know")
                      : s === "stt"
                        ? t("Transcription (STT)")
                        : s}
                  </option>
                ))}
              </select>
            </label>
            <p className="caption">
              {t("Capture: recording absent. STT: audio differs from transcript. Retrieval: expected transcript was not selected. Generation: supplied evidence was misrepresented. Leave unknown if unsure.")}{" "}</p>
            <label>
              {t("Find an expected recording (optional)")}{" "}<input
                value={search}
                maxLength={200}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="text-button"
              onClick={() => setQuery(search.trim())}
            >
              {t("Search recordings")}{" "}</button>
            {recordings.error ? (
              <ErrorState
                message={recordings.error}
                retry={recordings.refresh}
              />
            ) : null}
            {recordings.data?.items.map((source) => (
              <label className="feedback-source-choice" key={source.id}>
                <input
                  type="checkbox"
                  checked={expectedIDs.includes(source.id)}
                  disabled={
                    !expectedIDs.includes(source.id) && expectedIDs.length >= 30
                  }
                  onChange={(e) => {
                    setExpected(
                      (e.target.checked
                        ? [...expectedIDs, source.id]
                        : expectedIDs.filter((id) => id !== source.id)
                      ).join(","),
                    );
                    setId(newId());
                  }}
                />
                {new Date(source.started_at).toLocaleString(displayLocale())} ·{" "}
                {(source.text || statusLabel(source.state)).slice(0, 160)}
              </label>
            ))}
            {expectedIDs.length ? (
              <p className="caption">
                {expectedIDs.length} {t("expected recordings selected.")}{" "}
                <button
                  type="button"
                  className="text-button"
                  onClick={() => {
                    setExpected("");
                    setId(newId());
                  }}
                >
                  {t("Clear selection")}{" "}</button>
              </p>
            ) : null}
            {outputText ? (
              <>
                <button
                  type="button"
                  className="text-button"
                  onClick={() => {
                    setExcerpt(outputText.slice(0, 1000));
                    setId(newId());
                  }}
                >
                  {t("Use beginning of shown answer as passage")}{" "}</button>
                <label>
                  {t("Passage to include (optional)")}{" "}<textarea
                    maxLength={1000}
                    value={excerpt}
                    onChange={(e) => {
                      setExcerpt(e.target.value);
                      setId(newId());
                    }}
                  />
                </label>
              </>
            ) : null}
            <label>
              {t("What was wrong or missing?")}{" "}<textarea
                maxLength={2000}
                value={note}
                onChange={(e) => {
                  setNote(e.target.value);
                  setId(newId());
                }}
              />
            </label>
            <p className="caption">
              {t("Only references and this note are stored locally. Feedback does not edit transcripts or train a model.")}{" "}</p>
            <button className="button small" disabled={busy}>
              {busy ? t("Saving…") : t("Save feedback")}
            </button>
            {error ? <ErrorState message={error} /> : null}
          </fieldset>
        </form>
      ) : null}
    </div>
  );
}
