import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, newId, post } from "./api";
import { allDayFilters, dayFilterKey, dayFilterParams, hasDayFilters, recapMatchesDayFilters, type DayFilters } from "./dayFilters";
import { t } from "./i18n";
import { profileResourceUrl } from "./profiles";
import { dateTimeLabel, timeLabel } from "./format";
import { CompletenessDetails, EvidenceFeedback } from "./CompletenessFeedback";
import { ActionPoints } from "./ActionPoints";
import { recapBrowserTopics, type RecapFocus } from "./recapItems";
import { DayRecapOverview } from "./DayRecapOverview";
import { SourceList } from "./SourceList";
import { RecapContent } from "./RecapContent";
import { SummaryTranslation } from "./SummaryTranslation";
import type { ChatScope, Recap, Source, Status } from "./types";
import { useResource } from "./useResource";
import { ErrorState, Loading, Notice, RichText } from "./UI";
import { Icon } from "./Icon";
import { DisplayTranslationLanguage } from "./displayTranslations";
import { requestedRecapTopic, subscribeNavigation } from "./routing";

const emptySources: Source[] = [];

export function RecapSection({
  day,
  timezone,
  status,
  statusError,
  onUpload,
  onChat,
  compact = false,
  filters = allDayFilters,
}: {
  day: string;
  timezone: string;
  status?: Status;
  statusError?: string;
  onUpload?: () => void;
  onChat: (scope: ChatScope, question?: string) => void;
  compact?: boolean;
  filters?: DayFilters;
}) {
  const filtered = hasDayFilters(filters);
  const query = new URLSearchParams(dayFilterParams(filters)).toString();
  const resource = useResource<Recap>(`/v1/recaps/${day}${query ? `?${query}` : ""}`, 15_000);
  const mismatch = resource.data && !recapMatchesDayFilters(resource.data, filters);
  const validData = mismatch || filtered && resource.error ? undefined : resource.data;
  const recap = { ...resource, error: resource.error || (mismatch ? t("The recap does not match the selected filters. Refresh and try again.") : ""),
    data: filtered && validData?.stale ? { ...validData, summary: null, output_id: undefined, evidence: undefined } : validData };
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState("");
  const retryID = useRef<string | undefined>(undefined);
  async function retryFilteredRecap() {
    if (retrying) return;
    setRetrying(true); setRetryError(""); retryID.current ??= newId();
    try {
      await post("/v1/jobs", { id: retryID.current, kind: "recap", parameters: { day, ...dayFilterParams(filters) } });
      retryID.current = undefined; resource.setData(undefined); resource.refresh();
    } catch (error) { setRetryError(errorMessage(error)); }
    finally { setRetrying(false); }
  }
  const filteredMessage = recap.data?.no_matching_sources ? t("No recordings match the selected filters for this day.")
    : recap.data?.stale && recap.data.generation_state !== "failed" ? t("Preparing a recap for the selected filters…")
    : emptyRecapMessage(recap.data, false);
  const scopedChat = filtered ? undefined : onChat;
  const statusAt = status?.as_of ? Date.parse(status.as_of) : NaN;
  const summarizing = !statusError && Number.isFinite(statusAt) && Date.now() - statusAt <= 120_000 &&
    (status?.generation?.stages.summarizing || 0) > 0;
  const [showVersions, setShowVersions] = useState(false);
  const [focus, setFocus] = useState<RecapFocus>();
  const [topicRequest, setTopicRequest] = useState<{ title: string; request: number }>();
  useEffect(() => {
    const openRequestedTopic = () => {
      if (filtered) return;
      const title = requestedRecapTopic(window.location.pathname, window.location.search, day);
      if (title) setTopicRequest((previous) => ({ title, request: (previous?.request || 0) + 1 }));
    };
    openRequestedTopic();
    return subscribeNavigation(openRequestedTopic);
  }, [day, filtered]);
  const summary = recap.data?.summary || "";
  const version = recap.data?.output_id || recap.data?.generated_at || "unversioned";
  const sources = recap.data?.sources || emptySources;
  const hasOriginalTopics = recapBrowserTopics([], sources).length > 0;
  useEffect(() => { setFocus(undefined); }, [day, recap.data?.summary, recap.data?.output_id]);
  useEffect(() => {
    window.addEventListener("omi:archive-changed", recap.refresh);
    return () =>
      window.removeEventListener("omi:archive-changed", recap.refresh);
  }, [recap.refresh]);
  const overview = (language?: string) => <DisplayTranslationLanguage value={language}>
    <ActionPoints key={`actions-${day}`} day={day} timezone={timezone} filters={filters}
      focus={focus && recap.data?.stale && focus.basis !== "original" ? { ...focus, passages: [] } : focus} onChat={scopedChat} />
  </DisplayTranslationLanguage>;
  return (
    <section className="recap-section">
      <div className="section-heading">
        <div>
          <h2>{filtered ? t("Filtered recap") : t("Day recap")}</h2>
        </div>
      </div>
      {!filtered ? <DayRecapOverview key={day} day={day} timezone={timezone} onTopic={(title) => {
        setTopicRequest((previous) => ({ title, request: (previous?.request || 0) + 1 }));
      }} /> : null}
      {!filtered ? <h3>{t("Topics")}</h3> : null}
      {recap.error ? <ErrorState message={recap.error} retry={recap.refresh} /> : null}
      {filtered && recap.data?.generation_state === "failed" ? <ErrorState
        message={retryError || recap.data.generation_error || t("The filtered recap could not be generated.")}
        retry={() => void retryFilteredRecap()} /> : null}
      {retrying ? <Loading>{t("Preparing a recap for the selected filters…")}</Loading> : null}
      {!filtered && recap.data?.stale ? (
        <Notice>
          {t("New or changed evidence is available. This recap will update automatically.")}{" "}</Notice>
      ) : null}
      {recap.data && !recap.data.no_matching_sources ? <RecapCoverage recap={recap.data.summary ? { ...recap.data, boundary_note: undefined } : recap.data} timezone={timezone} /> : null}
      {!compact && !filtered ? <CompletenessDetails value={recap.data?.completeness} /> : null}
      {recap.loading && !recap.data ? (
        <Loading>{filtered ? t("Loading filtered recap…") : t("Loading recap…")}</Loading>
      ) : recap.data && (recap.data.summary || hasOriginalTopics) ? (
        <div className="recap-content">
          {!filtered && recap.data.summary ? <SummaryTranslation outputId={recap.data.output_id} original={recap.data.summary}
            renderFollowing={overview}
            renderContent={(displayText, translated, translation, language) => <DisplayTranslationLanguage value={language}><RecapContent
                key={`${day}:${version}:${summary}`}
                text={summary}
                translatedText={translated ? displayText : undefined}
                translationStructure={translated ? translation?.structure : undefined}
                day={day}
                version={version}
                sources={sources}
                timezone={timezone}
                onCorrect={recap.refresh}
                onChat={scopedChat}
                onFocus={setFocus}
                compact={compact}
                topicRequest={topicRequest}
              /></DisplayTranslationLanguage>
            } /> : <RecapContent
            key={`${day}:${dayFilterKey(filters)}:${recap.data.output_id || recap.data.generated_at || "originals"}:${recap.data.summary || ""}`}
            text={recap.data.summary || ""} day={day} version={recap.data.output_id || "originals"}
            sources={sources} timezone={timezone} onCorrect={recap.refresh} onChat={scopedChat}
            onFocus={setFocus} compact={compact} topicRequest={topicRequest} emptyMessage={filtered ? filteredMessage : undefined} />}
          {filtered || !recap.data.summary ? overview() : null}
          {recap.data.pending_chunks ? (
            <p className="caption">
              {recap.data.pending_chunks} {t("recording(s) were awaiting transcription when generated. This recap updates automatically to include later evidence.")}{" "}</p>
          ) : null}
          {!filtered && recap.data.summary ? <RecapUtilities compact={compact} generatedAt={recap.data.generated_at} timezone={timezone}>
          {recap.data.boundary_note ? <p className="caption">{t(recap.data.boundary_note)}</p> : null}
          <EvidenceFeedback outputId={recap.data.output_id} evidence={recap.data.evidence} outputText={recap.data.summary} />
          <div className="recap-footer">
            {recap.data.generated_at ? (
              <span className="caption">
                {t("Generated at")}{" "}{timeLabel(recap.data.generated_at, timezone)}
              </span>
            ) : null}
            <a
              className="text-link caption"
              href={profileResourceUrl(`/v1/diary/${day}.md`)}
              download
            >
              <Icon name="download" size={14} />
              {t("Export Markdown")}{" "}</a>
            <button
              className="text-button"
              onClick={() => setShowVersions((value) => !value)}
            >
              {showVersions ? t("Hide saved versions") : t("Saved versions")}
            </button>
          </div>
          {showVersions ? (
            <RecapVersions day={day} timezone={timezone} />
          ) : null}
          {recap.data.sources?.length ? (
            <details className="recap-sources">
              <summary>
                {t("View")}{" "}{recap.data.sources.length} {t("source recording(s)")}{" "}</summary>
              <SourceList
                sources={recap.data.sources}
                timezone={timezone}
                onCorrect={recap.refresh}
              />
            </details>
          ) : null}
          {compact ? <CompletenessDetails value={recap.data.completeness} /> : null}
          </RecapUtilities> : null}
          {filtered && recap.data.sources?.length ? <details className="recap-sources">
            <summary>{t("View")} {recap.data.sources.length} {t("source recording(s)")}</summary>
            <SourceList sources={recap.data.sources} timezone={timezone} onCorrect={recap.refresh} />
          </details> : null}
        </div>
      ) : filtered ? recap.error ? null : <p className="caption recap-filtered-empty">{filteredMessage}</p> : (
        <div className="recap-empty">
          <span className="empty-symbol"><Icon name="diary" size={25} /></span>
          {compact ? <div>
            <h3>{t("Your recap comes together automatically.")}</h3>
            <p>{emptyRecapMessage(recap.data, summarizing)}</p>
            {onUpload ? <button className="text-button recap-upload" onClick={onUpload}>{t("Add a recording")}</button> : null}
          </div> : <>
            <h3>{t("The story of your day starts here.")}</h3>
            <p>{emptyRecapMessage(recap.data, summarizing)}</p>
            {onUpload ? <button className="button accent recap-upload" onClick={onUpload}><Icon name="upload" size={17} />{t("Add a recording")}</button> : null}
          </>}
        </div>
      )}
      {compact && !filtered && !recap.data?.summary ? <CompletenessDetails value={recap.data?.completeness} /> : null}
      {(!filtered || recap.data) && !recap.data?.summary && !hasOriginalTopics ? <ActionPoints key={`actions-${day}`} day={day} timezone={timezone} filters={filters}
        focus={focus && recap.data?.stale && focus.basis !== "original" ? { ...focus, passages: [] } : focus} onChat={scopedChat} /> : null}
    </section>
  );
}

function emptyRecapMessage(recap?: Recap, summarizing = false): string {
  if (summarizing) return t("Your recap is being generated now. It will appear here when ready.");
  if (recap?.no_recap_content)
    return t("No useful recap content in transcribed recordings yet. Your recordings remain available.");
  if (recap?.waiting_for_pause)
    return t("Live conversations are waiting for a longer pause before being summarized.");
  if (recap?.deferred_sources || recap?.pending_chunks)
    return t("Some recordings are awaiting upload or transcription.");
  return t("Uploaded recordings are summarized after transcription. Live conversations wait for a longer pause.");
}

export function RecapCoverage({ recap, timezone }: { recap: Recap; timezone: string }) {
  return <div className="recap-coverage caption" role="status">
    {recap.included_through ? <p>{t("Includes transcribed recordings through")}{" "}{timeLabel(recap.included_through, timezone)}.</p>
      : recap.included_through === null ? <p>{t("No transcribed recordings included yet.")}</p> : null}
    {recap.waiting_for_pause ? <p>{t("Live conversations are waiting for a longer pause before being summarized.")}</p> : null}
    {recap.pending_upload_sources || recap.pending_chunks || (!recap.waiting_for_pause && recap.deferred_sources)
      ? <p>{t("Some recordings are awaiting upload or transcription.")}</p> : null}
    {recap.boundary_note ? <p>{t(recap.boundary_note)}</p> : null}
  </div>;
}

function RecapUtilities({ compact, generatedAt, timezone, children }: { compact: boolean; generatedAt?: string; timezone: string; children: ReactNode }) {
  return compact ? <details className="recap-utilities">
    <summary><span>{t("Sources, export & history")}</span>{generatedAt ? <span>{t("Generated at")}{" "}{timeLabel(generatedAt, timezone)}</span> : null}</summary>
    {children}
  </details> : <>{children}</>;
}

function RecapVersions({ day, timezone }: { day: string; timezone: string }) {
  const resource = useResource<{
    items: { id: string; created_at: string; content: Recap }[];
  }>(`/v1/diary/${day}/versions`);
  return (
    <div className="recap-history">
      {resource.error ? (
        <ErrorState message={resource.error} retry={resource.refresh} />
      ) : resource.loading ? (
        <Loading>{t("Loading saved versions…")}</Loading>
      ) : resource.data?.items.length ? (
        resource.data.items.map((version) => (
          <details key={version.id}>
            <summary>{dateTimeLabel(version.created_at, timezone)}</summary>
            {version.content.summary ? <SummaryTranslation outputId={version.id} original={version.content.summary} />
              : <RichText text={t("No summary in this version.")} />}
          </details>
        ))
      ) : (
        <p className="caption">{t("No previous versions saved.")}</p>
      )}
    </div>
  );
}

