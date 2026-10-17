import { t } from "./i18n";
import { useEffect, useRef, useState } from "react";
import { errorMessage, post, rewindAudioUrl } from "./api";
import { dateTimeLabel, timeLabel } from "./format";
import { Icon } from "./Icon";
import { CompletenessDetails, EvidenceFeedback } from "./CompletenessFeedback";
import { SourceList } from "./SourceList";
import type { MemoryWindow } from "./types";
import { EmptyState, ErrorState, Notice, RichText } from "./UI";

export type RewindIntent = { minutes: number; id: number };

export function Rewind({
  timezone,
  intent,
}: {
  timezone: string;
  intent?: RewindIntent;
}) {
  const [minutes, setMinutes] = useState(intent?.minutes || 10);
  const [summarize, setSummarize] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [audioError, setAudioError] = useState(false);
  const [result, setResult] = useState<MemoryWindow>();
  const [completedMinutes, setCompletedMinutes] = useState(10);
  const handled = useRef<number | undefined>(undefined);
  async function rewind(duration = minutes, includeSummary = summarize) {
    setBusy(true);
    setError("");
    setAudioError(false);
    setResult(undefined);
    // Keep the exact end timestamp for both the transcript and audio request.
    const end = new Date().toISOString();
    try {
      setResult(
        await post<MemoryWindow>("/v1/rewind", {
          minutes: duration,
          end,
          summarize: includeSummary,
        }),
      );
      setCompletedMinutes(duration);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (intent && handled.current !== intent.id) {
      handled.current = intent.id;
      setMinutes(intent.minutes);
      void rewind(intent.minutes);
    }
    // A new intent is an explicit navigation action, not a form-state dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intent]);
  const hasAudio = result?.sources.some((source) => source.audio_available !== false);
  return (
    <div className="feature-content">
      <form
        className="rewind-form"
        onSubmit={(event) => {
          event.preventDefault();
          void rewind();
        }}
      >
        <label htmlFor="rewind-minutes">
          {t("Last")}{" "}
          <input
            id="rewind-minutes"
            type="number"
            min={1}
            max={60}
            step={1}
            value={minutes}
            onChange={(event) => setMinutes(Number(event.target.value))}
            required
          />{" "}
          {t("minutes")}{" "}</label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={summarize}
            onChange={(event) => setSummarize(event.target.checked)}
          />
          {t("Include a quick recap")}{" "}</label>
        <button className="button dark" disabled={busy}>
          <Icon name="rewind" />
          {busy ? t("Rewinding…") : t("Rewind")}
        </button>
      </form>
      {error ? <ErrorState message={error} /> : null}
      {result ? (
        <div className="rewind-result">
          <div className="section-heading">
            <h2>
              {timeLabel(result.start, timezone)}–
              {timeLabel(result.end, timezone)}
            </h2>
            <span className="caption">
              {dateTimeLabel(result.end, timezone)}
            </span>
          </div>
          {result.sources.length ? (
            <>
              {result.sources.some((source) => source.source_kind === "omi_audio_webhook") ? <Notice>
                {t("Official audio times estimate server receipt. Bursts or multiple senders can overlap in combined playback. Play individual sources below to hear every accepted chunk; this timeline does not establish continuous capture.")}{" "}</Notice> : null}
              {hasAudio ? <div className="playback-panel">
                <div>
                  <h3>{t("Listen back")}</h3>
                  <p className="caption">
                    {t("Audio is clipped to the requested window. Gaps remain silent.")}{" "}</p>
                </div>
                <audio
                  key={result.end}
                  controls
                  preload="none"
                  src={rewindAudioUrl(completedMinutes, result.end)}
                  onError={() => setAudioError(true)}
                  aria-label={t("Play rewind audio")}
                />
                {audioError ? (
                  <ErrorState message={t("Audio for this window is unavailable. You can still read the available transcripts below.")} />
                ) : null}
              </div> : (
                <Notice>{t("Original audio is unavailable for these sources. You can read the transcripts below.")}</Notice>
              )}
              {result.summary ? (
                <section className="rewind-summary">
                  <h2>{t("What you heard")}</h2>
                  <RichText text={result.summary} />
                  <EvidenceFeedback outputId={result.output_id} evidence={result.evidence} outputText={result.summary} />
                </section>
              ) : summarize ? (
                <Notice>
                  {t("No transcribed speech is available to summarize yet.")}{" "}{hasAudio ? t("You can listen to the recording above.") : t("Original audio is unavailable.")}
                </Notice>
              ) : null}
              {result.pending_chunks ? (
                <Notice>
                  {result.pending_chunks} {t("recording(s) were awaiting transcription at this snapshot. Run rewind again to update.")}{" "}</Notice>
              ) : null}
              <CompletenessDetails value={result.completeness} />
              {result.audio_gaps.length ? (
                <p className="caption">
                  {t("There")}{" "}
                  {result.audio_gaps.length === 1
                    ? t("is 1 gap")
                    : t("are {value0} gaps", { value0: result.audio_gaps.length })}{" "}
                  {t("in recorded audio for this window.")}{" "}</p>
              ) : null}
              <div className="section-heading">
                <h2>{t("Source recordings")}</h2>
              </div>
              <p className="caption">
                {t("Transcripts include whole overlapping recording chunks. Markers and meeting gestures are approximate context hints.")}{" "}</p>
              <SourceList sources={result.sources} timezone={timezone} />
            </>
          ) : (
            <EmptyState icon="rewind" title={t("No audio in this window")}>
              {t("Try a longer window, or connect your iPhone to send its queued recordings.")}{" "}</EmptyState>
          )}
        </div>
      ) : !busy && !error ? (
        <EmptyState icon="rewind" title={t("A moment worth coming back to")}>
          {t("Choose how far back to listen. Your recap and source recordings will appear here.")}{" "}</EmptyState>
      ) : null}
    </div>
  );
}
