import { useEffect, useRef, useState } from "react";
import { dateTimeLabel, localDay } from "./format";
import { Icon } from "./Icon";
import { t } from "./i18n";
import { RecapEvidence } from "./RecapEvidence";
import { dayFromPath, subscribeNavigation } from "./routing";
import type { Source } from "./types";
import { EmptyState, ErrorState, Loading } from "./UI";
import { useResource } from "./useResource";

type Passage = { start_offset: number; end_offset: number; start_seconds?: number; end_seconds?: number };
type Hit = { id: string; started_at: string; title?: string | null; transcript_version: string;
  match: Passage & { text: string } };

export function archiveHitPath(hit: Hit, timezone: string) {
  const params = new URLSearchParams({ source: hit.id, version: hit.transcript_version,
    start: String(hit.match.start_offset), end: String(hit.match.end_offset) });
  return `/diary/${localDay(timezone, new Date(hit.started_at))}?${params}`;
}

export function ArchiveSearch({ timezone }: { timezone: string }) {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const resource = useResource<{ items: Hit[] }>(submitted ? `/v1/archive/search?${new URLSearchParams({ q: submitted })}` : null);
  useEffect(() => {
    window.addEventListener("omi:archive-changed", resource.refresh);
    return () => window.removeEventListener("omi:archive-changed", resource.refresh);
  }, [resource.refresh]);
  return <section className="archive-search" aria-label={t("Search the archive")}>
    <form className="search-form" role="search" onSubmit={(event) => {
      event.preventDefault();
      if (query.trim() === submitted) resource.refresh();
      setSubmitted(query.trim());
    }}>
      <Icon name="search" size={19} />
      <input type="search" value={query} onChange={(event) => setQuery(event.target.value)}
        maxLength={200} placeholder={t("Search transcripts across all days")} aria-label={t("Search the archive")} />
      <button className="text-button" type="submit">{t("Search")}</button>
      {submitted ? <button className="text-button" type="button" onClick={() => { setQuery(""); setSubmitted(""); }}>{t("Clear")}</button> : null}
    </form>
    {submitted ? <div className="archive-results" aria-live="polite">
      {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} />
        : resource.loading ? <Loading>{t("Searching transcripts…")}</Loading>
          : resource.data?.items.length ? <ul>{resource.data.items.map((hit) => <li key={hit.id}>
            <a href={archiveHitPath(hit, timezone)}>
              <span className="caption">{dateTimeLabel(hit.started_at, timezone)}</span>
              <strong>{hit.title || t("Recording")}</strong>
              <span className="archive-excerpt">{hit.match.text}</span>
            </a>
          </li>)}</ul> : <EmptyState icon="search" title={t("No matching transcripts")}>
            {t("Try another name, phrase, or keyword.")}
          </EmptyState>}
    </div> : null}
  </section>;
}

function passagePath(day: string) {
  if (dayFromPath(window.location.pathname) !== day) return null;
  const params = new URLSearchParams(window.location.search);
  const id = params.get("source");
  if (!id || !["version", "start", "end"].every((key) => params.has(key))) return null;
  const span = new URLSearchParams();
  for (const key of ["version", "start", "end"]) span.set(key, params.get(key)!);
  return `/v1/archive/sources/${encodeURIComponent(id)}?${span}`;
}

export function ArchivePassage({ day, timezone }: { day: string; timezone: string }) {
  const [path, setPath] = useState(() => passagePath(day));
  const panel = useRef<HTMLElement>(null);
  useEffect(() => subscribeNavigation(() => setPath(passagePath(day))), [day]);
  const resource = useResource<{ source: Source; passage: Passage }>(path, 15_000);
  useEffect(() => { if (path) panel.current?.focus(); }, [path]);
  useEffect(() => {
    const refresh = () => { resource.setData(undefined); resource.refresh(); };
    window.addEventListener("omi:archive-changed", refresh);
    return () => window.removeEventListener("omi:archive-changed", refresh);
  }, [resource.refresh, resource.setData]);
  if (!path) return null;
  return <section ref={panel} tabIndex={-1} className="archive-passage" aria-label={t("Search passage")}>
    <div className="section-heading"><h2>{t("Search passage")}</h2>
      <a className="text-link caption" href={`/diary/${day}`}>{t("Close passage")}</a></div>
    {resource.error ? <ErrorState message={resource.error} /> : resource.data ? <RecapEvidence key={path}
      source={resource.data.source} timezone={timezone} onCorrect={resource.refresh} point="" search
      sections={[{ ...resource.data.passage, title: resource.data.source.title || t("Recording") }]} />
      : <Loading />}
  </section>;
}
