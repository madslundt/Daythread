import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { audioUrl, recordingAudioUrl } from "./api";
import { ChatMarkdown } from "./ChatMarkdown";
import type { CitationTarget } from "./chatCitations";
import { chatSourceGroups, type ChatSourceEntry, type ChatSourceGroup } from "./chatSources";
import { dateTimeLabel } from "./format";
import { Icon } from "./Icon";
import { t } from "./i18n";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";
import { playbackTime } from "./recordingWaveform";
import type { ChatMessage, Source } from "./types";

function sourceDate(source: Source, timezone: string) {
  return Number.isFinite(Date.parse(source.started_at)) ? dateTimeLabel(source.started_at, timezone) : "";
}

function canPlay(source: Source) {
  return source.audio_available !== false && source.audio_availability !== "missing";
}

function firstEntry(group: ChatSourceGroup) {
  return group.entries.find((entry) => entry.cited) ?? group.entries[0];
}

function partCount(count: number) {
  return t("{count} nearby parts", { count });
}

export function ChatAnswer({ message, timezone, onInspect }: {
  message: ChatMessage; timezone: string; onInspect: () => void;
}) {
  const { content, sources, stale } = message;
  const groups = useMemo(() => chatSourceGroups(sources, content, stale), [sources, content, stale]);
  const citations = useMemo(() => new Map(groups.flatMap((group) => group.entries.filter((entry) => entry.cited)
    .map(({ source }) => [source.id.toLowerCase(), { number: group.number, id: source.id }] as const))), [groups]);
  const [selection, setSelection] = useState<CitationTarget>();
  const trigger = useRef<HTMLButtonElement>(null);
  const selectedGroup = selection && groups.find((group) => group.entries.some(({ source }) => source.id === selection.id));
  const cited = groups.filter((group) => group.cited);
  const related = groups.filter((group) => !group.cited);
  const open = useCallback((target: CitationTarget, button: HTMLButtonElement) => {
    onInspect();
    trigger.current = button;
    setSelection(target);
  }, [onInspect]);
  const close = useCallback(() => {
    setSelection(undefined);
    trigger.current?.focus({ preventScroll: true });
  }, []);

  function cards(items: ChatSourceGroup[]) {
    return <ul className="chat-source-cards">{items.map((group) => {
      const { source, passages } = firstEntry(group);
      const preview = passages[0]?.text || source.text;
      const title = source.title || t("Recording");
      const date = sourceDate(source, timezone);
      return <li key={group.key}>
        <button type="button" className="chat-source-card" aria-haspopup="dialog"
          aria-label={t("Open source {number}: {title}", { number: group.number, title })}
          onClick={(event) => open({ number: group.number, id: source.id }, event.currentTarget)}>
          <span className="chat-source-number" aria-hidden="true">{group.number}</span>
          <span className="chat-source-card-info">
            <strong>{title}</strong>
            {date || group.entries.length > 1 ? <small>{[date, group.entries.length > 1 && partCount(group.entries.length)].filter(Boolean).join(" \u00b7 ")}</small> : null}
            {preview ? <span className="chat-source-preview">{preview}</span> : null}
          </span>
          <Icon name="chevron-right" size={17} />
        </button>
      </li>;
    })}</ul>;
  }

  return <>
    {stale ? <p className="caption" role="status">{t("Historical answer: its transcript evidence has changed. Ask again for a current answer.")}</p> : null}
    <ChatMarkdown text={content} citations={citations} onCitation={open} />
    {message.retrieval_details?.notice ? <p className="caption">{t(message.retrieval_details.notice)}</p> : null}
    {groups.length ? <div className="chat-sources">
      {cited.length ? <><h3>{t("Cited sources")}</h3>{cards(cited)}</> : null}
      {related.length ? <details className="chat-related-sources">
        <summary onClick={onInspect}>{t("Related sources")} ({related.length})</summary>
        {cards(related)}
      </details> : null}
    </div> : null}
    {selectedGroup && selection ? <ChatSourceDialog group={selectedGroup} initialId={selection.id}
      timezone={timezone} stale={stale} onClose={close} /> : null}
  </>;
}

function ChatSourceDialog({ group, initialId, timezone, stale, onClose }: {
  group: ChatSourceGroup; initialId: string; timezone: string; stale?: boolean; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const [activeId, setActiveId] = useState(initialId);
  const entry = group.entries.find(({ source }) => source.id === activeId) ?? firstEntry(group);
  const { source } = entry;
  const title = source.title || t("Recording");
  const date = sourceDate(source, timezone);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => { element?.close(); };
  }, []);
  function dismiss() {
    dialog.current?.close();
    onClose();
  }

  return <dialog ref={dialog} className="chat-source-dialog" aria-labelledby={heading} onCancel={(event) => {
    event.preventDefault(); dismiss();
  }}>
    <header className="chat-source-dialog-header">
      <div>
        <span className="chat-source-eyebrow">{t("Source {number}", { number: group.number })}</span>
        <h2 id={heading}>{title}</h2>
        {date ? <time dateTime={source.started_at}>{date}</time> : null}
      </div>
      <button className="chat-source-close" type="button" autoFocus aria-label={t("Close")} onClick={dismiss}><Icon name="close" /></button>
    </header>
    <div className="chat-source-dialog-body">
      {stale ? <p className="caption">{t("Saved transcript only. Playback is turned off because the evidence has changed.")}</p> : null}
      {group.entries.length > 1 ? <label className="chat-source-parts">
        <span>{partCount(group.entries.length)}</span>
        <select value={source.id} onChange={(event) => setActiveId(event.target.value)}>
          {group.entries.map(({ source: part, cited }, index) => <option key={part.id} value={part.id}>
            {[t("Part {number}", { number: index + 1 }), sourceDate(part, timezone), part.title, group.cited && !cited && t("Related source")].filter(Boolean).join(" \u00b7 ")}
          </option>)}
        </select>
      </label> : null}
      <ChatSourceContent key={source.id} entry={entry} stale={stale} />
    </div>
  </dialog>;
}

function ChatSourceContent({ entry: { source, passages }, stale }: { entry: ChatSourceEntry; stale?: boolean }) {
  const [index, setIndex] = useState(0);
  const [full, setFull] = useState(false);
  const excerpt = passages[index] ?? passages[0];
  const timing = excerpt?.timing;
  const available = !stale && canPlay(source);
  const src = source.reference_type === "recording_transcription"
    ? recordingAudioUrl(source.recording_id || source.id) : audioUrl(source.id);
  const duration = (Date.parse(source.ended_at) - Date.parse(source.started_at)) / 1000;
  const clip = timing ? `${src}#t=${timing.start.toFixed(6)},${timing.end.toFixed(6)}` : undefined;

  return <>
    {passages.length > 1 ? <label className="chat-source-parts">
      <span>{t("Transcript excerpts")}</span>
      <select value={index} onChange={(event) => { setFull(false); setIndex(Number(event.target.value)); }}>
        {passages.map((passage, i) => <option value={i} key={passage.from}>
          {t("Excerpt {number}", { number: i + 1 })}{passage.timing ? ` \u00b7 ${playbackTime(passage.timing.start)}` : ""}
        </option>)}
      </select>
    </label> : null}
    {excerpt ? <section className="chat-source-excerpt">
      <h3>{t("Matching transcript excerpt")}</h3>
      <blockquote>{excerpt.text}</blockquote>
      {available && clip && timing ? <>
        <p className="caption">{`${t("Audio around this excerpt")} \u00b7 ${playbackTime(timing.start)}\u2013${playbackTime(timing.end)}`}</p>
        {!full ? <RecordingAudioPlayer src={clip} label={t("Audio around this excerpt")} showWaveform={false} /> : null}
      </> : available ? <p className="caption">{t("No timing for this excerpt. You can still play the full part.")}</p> : null}
    </section> : null}
    {!stale && !available ? <p className="caption">{t("Original audio is unavailable.")}</p> : null}
    {excerpt ? <details className="chat-source-full" open={full} onToggle={(event) => {
      if (event.target === event.currentTarget) setFull(event.currentTarget.open);
    }}>
      <summary>{available ? t("Full transcript and audio") : t("Full transcript")}</summary>
      {full ? <>{available ? <RecordingAudioPlayer src={src} label={t("Play full recording part")}
        durationHint={duration} showWaveform={false} /> : null}
        <p className="chat-source-transcript">{source.text || t("No transcript text")}</p></> : null}
    </details> : <section className="chat-source-excerpt">
      <h3>{t("Full transcript")}</h3>
      {available ? <RecordingAudioPlayer src={src} label={t("Play full recording part")} durationHint={duration} showWaveform={false} /> : null}
      <p className="chat-source-transcript">{source.text || t("No transcript text")}</p>
    </section>}
  </>;
}
