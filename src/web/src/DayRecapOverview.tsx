import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, post } from "./api";
import { dateTimeLabel } from "./format";
import { t } from "./i18n";
import { useResource } from "./useResource";
import { ErrorState, InlineText, Loading, RichText } from "./UI";

export type DayOverviewTopic = { id: string; title: string; summary?: string };
export type DayOverview = {
  day: string; summary: string | null; generated_at: string | null;
  stale: boolean; can_generate: boolean; topics: DayOverviewTopic[]; reading_minutes: number;
};

export function DayRecapOverview({ day, timezone, onTopic }: {
  day: string; timezone: string; onTopic: (title: string) => boolean;
}) {
  const path = `/v1/recaps/${day}/overview`;
  const resource = useResource<DayOverview>(path, 15_000);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<DayOverviewTopic>();
  const busy = useRef(false);
  const active = useRef(true);
  const generation = useRef(0);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    window.addEventListener("omi:archive-changed", resource.refresh);
    return () => window.removeEventListener("omi:archive-changed", resource.refresh);
  }, [resource.refresh]);
  const data = resource.data;
  useEffect(() => { setDetail(undefined); }, [data?.generated_at, data?.summary]);
  async function generate() {
    if (busy.current) return;
    busy.current = true; setGenerating(true); setError(""); setDetail(undefined);
    const attempt = ++generation.current;
    try {
      const result = await post<DayOverview>(path);
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
  function openTopic(topic: DayOverviewTopic) {
    setDetail(onTopic(topic.title) ? undefined : topic);
  }
  return <div className="day-recap-overview" aria-label={t("Day recap summary")} aria-busy={generating}>
    <div className="day-recap-overview-heading">
      <p className="caption">{t("The whole day, in five minutes or less.")}</p>
      {data?.can_generate && (!data.summary || data.stale || error) ? <button type="button" className="button accent"
        disabled={generating} onClick={() => void generate()}>
        {generating ? t("Generating recap…") : data.summary ? t("Update recap") : t("Recap")}
      </button> : null}
    </div>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {error ? <ErrorState message={error} retry={() => void generate()} /> : null}
    {generating ? <Loading>{t("Generating your day recap. You can keep reading the topics below.")}</Loading> : null}
    {resource.loading && !data ? <Loading>{t("Loading saved recap…")}</Loading> : null}
    {data?.summary ? <>
      <DayOverviewText text={data.summary} topics={data.topics || []} onTopic={openTopic} />
      <p className="caption">{data.generated_at ? <>{t("Generated at")} {dateTimeLabel(data.generated_at, timezone)} · </> : null}
        {t("{value0} min read", { value0: data.reading_minutes || 1 })}</p>
      {data.stale ? <p className="caption" role="status">{t("New recordings are available. Update the recap to include them.")}</p> : null}
    </> : data ? <p className="caption">{data.can_generate
      ? t("Generate a short recap of this day. It will be saved here for later.")
      : t("Your day recap will be available when there is something to recap.")}</p> : null}
    {detail ? <section className="day-recap-saved-topic" aria-label={detail.title}>
      <h3><InlineText text={detail.title} /></h3>
      <p className="caption">{t("This topic has changed. These are the details saved with your recap.")}</p>
      {detail.summary ? <RichText text={detail.summary} /> : <p className="caption">{t("Browse the topics below for the latest details.")}</p>}
      <button className="text-button" onClick={() => setDetail(undefined)}>{t("Close")}</button>
    </section> : null}
  </div>;
}

// Only validated topic links become interactive; model output never becomes HTML or an external URL.
export function DayOverviewText({ text, topics, onTopic }: {
  text: string; topics: DayOverviewTopic[]; onTopic: (topic: DayOverviewTopic) => void;
}) {
  function inline(value: string): ReactNode[] {
    return value.split(/(\[[^\]\n]+\]\(topic:[^\s)]*\))/g).map((part, index) => {
      const match = /^\[([^\]\n]+)\]\(topic:([^\s)]*)\)$/.exec(part);
      if (!match) return <InlineText key={index} text={part} />;
      let title: string;
      try { title = decodeURIComponent(match[2]); } catch { return match[1]; }
      const topic = topics.find((item) => item.id === title);
      return topic ? <a key={index} className="day-recap-topic-link" href={`#topic-${encodeURIComponent(title)}`}
        onClick={(event) => { event.preventDefault(); onTopic(topic); }}><InlineText text={match[1]} /></a> : match[1];
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
