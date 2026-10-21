import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, post } from "./api";
import { dateTimeLabel } from "./format";
import { t } from "./i18n";
import { RecapSummary } from "./RecapSummary";
import { useResource } from "./useResource";
import { navigatePath, recapTopicPath } from "./routing";
import { ErrorState, InlineText, Loading } from "./UI";

export type DayOverviewTopic = { id: string; title: string; summary?: string; day?: string };
export type DayOverview = {
  day: string; summary: string | null; generated_at: string | null;
  stale: boolean; can_generate: boolean; topics: DayOverviewTopic[]; reading_minutes: number;
  output_id?: string;
};
type SavedOverview = Omit<DayOverview, "day"> & { day?: string; week_start?: string; partial?: boolean; cutoff?: string };

export function DayRecapOverview({ day, timezone, onTopic }: {
  day: string; timezone: string; onTopic: (title: string) => void;
}) {
  return <SavedRecapOverview key={day} day={day} path={`/v1/recaps/${day}/overview`} timezone={timezone} onTopic={onTopic} />;
}

export function WeeklyRecapOverview({ week, timezone }: { week: string; timezone: string }) {
  return <SavedRecapOverview key={week} path={`/v1/weekly-reviews/${week}/overview`} timezone={timezone} weekly />;
}

function SavedRecapOverview({ path, day, timezone, onTopic, weekly = false }: {
  path: string; day?: string; timezone: string; onTopic?: (title: string) => void; weekly?: boolean;
}) {
  const resource = useResource<SavedOverview>(path, 15_000);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const busy = useRef(false);
  const active = useRef(true);
  const generation = useRef(0);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    window.addEventListener("omi:archive-changed", resource.refresh);
    return () => window.removeEventListener("omi:archive-changed", resource.refresh);
  }, [resource.refresh]);
  const data = resource.data;
  async function generate() {
    if (busy.current || !data?.can_generate) return;
    busy.current = true; setGenerating(true); setError("");
    const attempt = ++generation.current;
    try {
      const result = await post<SavedOverview>(path);
      if (active.current && attempt === generation.current) {
        resource.setData(result); resource.refresh();
      }
    } catch (failure) {
      if (active.current && attempt === generation.current) setError(errorMessage(failure));
    } finally {
      busy.current = false;
      if (active.current && attempt === generation.current) setGenerating(false);
    }
  }
  return <div className={`day-recap-overview${weekly ? " weekly-recap-overview" : ""}`}
    aria-label={weekly ? t("Weekly recap") : t("Day recap summary")} aria-busy={generating}>
    <div className="day-recap-overview-heading">
      <p className="caption">{weekly ? t("The whole week, in five minutes or less.") : t("The whole day, in five minutes or less.")}</p>
      {data?.can_generate ? <button type="button" className="button accent"
        disabled={generating} onClick={() => void generate()}>
        {generating ? t("Generating recap…") : data.summary ? t("Update recap") : t("Recap")}
      </button> : null}
    </div>
    {weekly && data?.partial && data.cutoff ? <p className="caption" role="status">
      {t("This week is still in progress. Evidence cutoff:")} {dateTimeLabel(data.cutoff, timezone)}
    </p> : null}
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {error ? <ErrorState message={error} retry={data?.can_generate ? () => void generate() : undefined} /> : null}
    {generating ? <Loading>{weekly ? t("Generating your weekly recap…") : t("Generating your day recap. You can keep reading the topics below.")}</Loading> : null}
    {resource.loading && !data ? <Loading>{weekly ? t("Loading saved weekly recap…") : t("Loading saved recap…")}</Loading> : null}
    {data?.summary ? <>
      <RecapSummary key={data.output_id || data.generated_at || path} outputId={data.output_id} text={data.summary}
        renderContent={(text) => <DayOverviewText text={text} day={day} topics={data.topics || []}
          onTopic={onTopic ? (topic) => onTopic(topic.title) : undefined} />} />
      <p className="caption">{data.generated_at ? <>{t("Generated at")} {dateTimeLabel(data.generated_at, timezone)} · </> : null}
        {t("{value0} min read", { value0: data.reading_minutes || 1 })}</p>
      {data.can_generate ? <p className="caption" role="status">{weekly
        ? t("New recordings are available. Update the weekly recap to include them.")
        : t("New recordings are available. Update the recap to include them.")}</p> : null}
    </> : data ? <p className="caption">{data.can_generate
      ? weekly ? t("Generate a short recap of this week. It will be saved here for later.") : t("Generate a short recap of this day. It will be saved here for later.")
      : weekly ? t("Your weekly recap will be available when there is something to recap.") : t("Your day recap will be available when there is something to recap.")}</p> : null}
  </div>;
}

// Only validated topic links become interactive; model output never becomes HTML or an external URL.
export function DayOverviewText({ text, day, topics, onTopic }: {
  text: string; day?: string; topics: DayOverviewTopic[]; onTopic?: (topic: DayOverviewTopic) => void;
}) {
  function inline(value: string): ReactNode[] {
    return value.split(/(\[[^\]\n]+\]\(topic:[^\s)]*\))/g).map((part, index) => {
      const match = /^\[([^\]\n]+)\]\(topic:([^\s)]*)\)$/.exec(part);
      if (!match) return <InlineText key={index} text={part} />;
      let title: string;
      try { title = decodeURIComponent(match[2]); } catch { return match[1]; }
      const topic = topics.find((item) => item.id === title);
      const href = topic ? recapTopicPath(topic.day || day, topic.title) : undefined;
      return topic && href ? <a key={index} className="day-recap-topic-link" href={href}
        onClick={(event) => {
          if ((event.button ?? 0) !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          if (onTopic) onTopic(topic);
          else navigatePath(href, { reselect: true });
        }}><InlineText text={match[1]} /></a> : match[1];
    });
  }
  return <div className="prose day-recap-prose">{text.split(/\n\s*\n/).map((block, index) => {
    const lines = block.trim().split("\n");
    if (!block.trim()) return null;
    if (lines.every((line) => /^[-*] /.test(line))) return <ul key={index}>{lines.map((line, i) => <li key={i}>{inline(line.slice(2))}</li>)}</ul>;
    if (/^#{1,4} /.test(lines[0])) return <Fragment key={index}><h3>{inline(lines[0].replace(/^#+\s*/, ""))}</h3>
      {lines.length > 1 ? <p>{inline(lines.slice(1).join("\n"))}</p> : null}</Fragment>;
    return <p key={index}>{inline(block)}</p>;
  })}</div>;
}
