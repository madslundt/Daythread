import { t } from "./i18n";
import { profileStorageKey } from "./profiles";
import { ImportanceButton, type ImportanceTarget } from "./Importance";
import { useId, useState } from "react";
import { Icon } from "./Icon";
import { itemSources, recapGroups, recapGroupSummary, recapParts, recapReadingText, recapTopicText, type RecapPart } from "./recapItems";
import { RecapEvidence } from "./RecapEvidence";
import { RecapTopic } from "./RecapTopic";
import { CopySummary } from "./CopySummary";
import type { ChatScope, Source } from "./types";
import { InlineText, RichText } from "./UI";

function savedChecks(key: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

export function RecapContent({ text, day, version, sources, timezone, onChat, onCorrect, compact = false }: {
  text: string;
  day: string;
  version: string;
  sources: Source[];
  timezone: string;
  onChat: (scope: ChatScope, question?: string) => void;
  onCorrect: () => void;
  compact?: boolean;
}) {
  const storageKey = profileStorageKey(`omi.recap-checks.v1:${day}:${version}`);
  const [checks, setChecks] = useState(() => savedChecks(storageKey));
  const [storageError, setStorageError] = useState(false);
  const parts = recapParts(text);
  const groups = recapGroups(parts, day);
  const savedVersion = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(version);
  const target = (section = ""): ImportanceTarget => ({ kind: "recap", id: version, section });
  function toggle(key: string) {
    const next = checks.includes(key) ? checks.filter((item) => item !== key) : [...checks, key];
    setChecks(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
      setStorageError(false);
    } catch {
      setStorageError(true);
    }
  }
  const renderPart = (part: RecapPart, index: number, topic = "") => part.kind === "heading" ? (
        <div key={index} className="recap-section-heading"><h3><InlineText text={part.text} /></h3>{savedVersion ? <ImportanceButton target={target(part.text)} label={part.text} /> : null}</div>
      ) : part.kind === "text" ? (
        <div key={index} className="recap-paragraph"><RichText text={compact ? recapReadingText(part.text) : part.text} />{savedVersion ? <ImportanceButton target={target(part.text)} label={t("recap section")} compact={compact} /> : null}</div>
      ) : (
        <RecapItem
          key={`${index}:${part.text}`}
          text={part.text}
          topic={topic}
          importanceTarget={savedVersion ? target(part.text) : undefined}
          action={part.action}
          checked={checks.includes(part.text)}
          onToggle={() => toggle(part.text)}
          sources={sources}
          timezone={timezone}
          onChat={() => onChat({ kind: "day", day }, `Tell me more about this point from my day recap: ${part.text}`)}
          onCorrect={onCorrect}
          compact={compact}
        />
      );
  return (
    <div className={`prose recap-items ${compact ? "recap-reading-view" : ""}`}>
      {compact ? <div className="recap-reading-tools">
        {savedVersion ? <ImportanceButton target={target()} label={t("day recap")} compact /> : null}
        <p className="caption recap-item-hint">{t("Open a topic for its summary. Original passages are available underneath.")}</p>
      </div> : <>
        {savedVersion ? <ImportanceButton target={target()} label={t("day recap")} /> : null}
        <p className="caption recap-item-hint">{t("One summary per topic, with original passages available underneath.")}</p>
      </>}
      {groups.map((group, index) => {
        const actions = group.parts.some((part) => part.kind === "item" && part.action);
        if (group.title && !actions) return <RecapTopic key={`${index}:${group.title}`}
          title={group.title} parts={group.parts} day={day} sources={sources} timezone={timezone}
          onChat={onChat} onCorrect={onCorrect} compact={compact}
          importanceTarget={savedVersion ? target(group.title) : undefined} />;
        // Keep existing optional task checks usable in older recaps.
        const copy = <CopySummary text={recapTopicText(group.parts).replace(/\*\*/g, "")} />;
        if (!group.title) return <div key={index}>{copy}{group.parts.map((part, itemIndex) => renderPart(part, itemIndex))}</div>;
        const content = <>{copy}{group.parts.map((part, itemIndex) => renderPart(part, itemIndex, group.title))}</>;
        return compact ? <details className="recap-group" key={`${index}:${group.title}`} open={actions}>
          <summary>
            <span className="recap-group-overview"><span><InlineText text={group.title} /></span><span className="recap-group-summary">{recapGroupSummary(group.parts)}</span></span>
            {savedVersion ? <ImportanceButton target={target(group.title)} label={group.title.replace(/\*\*/g, "")} compact /> : null}
            <Icon name="chevron-right" size={15} />
          </summary>
          <div className="recap-group-content">{content}</div>
        </details> : <section key={index}>{renderPart({ kind: "heading", text: group.title }, 0)}{content}</section>;
      })}
      {parts.some((part) => part.kind === "item" && part.action) ? (
        <p className="caption recap-check-hint" role={storageError ? "status" : undefined}>
          {storageError ? t("Checks are kept for this visit only because browser storage is unavailable.") : t("Optional checkmarks · saved in this browser. Inbox status is managed separately.")}
        </p>
      ) : null}
    </div>
  );
}

function RecapItem({ text, topic, importanceTarget, action, checked, onToggle, sources, timezone, onChat, onCorrect, compact }: {
  text: string;
  topic: string;
  importanceTarget?: ImportanceTarget;
  action: boolean;
  checked: boolean;
  onToggle: () => void;
  sources: Source[];
  timezone: string;
  onChat: () => void;
  onCorrect: () => void;
  compact: boolean;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const related = itemSources(text, sources);
  return (
    <div className={`recap-item ${action ? "recap-action" : ""} ${checked ? "is-checked" : ""}`}>
      <div className="recap-item-line">
        {action ? (
          <label className="recap-check-target">
            <input type="checkbox" checked={checked} onChange={onToggle} aria-label={`Mark complete: ${(compact ? recapReadingText(text) : text).replace(/\*\*/g, "")}`} />
          </label>
        ) : <span className="recap-bullet" aria-hidden="true">•</span>}
        <button type="button" className="recap-item-button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((value) => !value)}>
          <span><InlineText text={compact ? recapReadingText(text) : text} /></span>
          <Icon name="chevron-right" size={16} />
        </button>
        {compact && importanceTarget ? <ImportanceButton target={importanceTarget} label={`recap point: ${recapReadingText(text).replace(/\*\*/g, "")}`} compact /> : null}
      </div>
      {!compact && importanceTarget ? <ImportanceButton target={importanceTarget} label={t("recap point")} /> : null}
      <div id={id} hidden={!open} className="recap-item-detail">
        {open ? <>
          <button type="button" className="button small" onClick={onChat}><Icon name="chat" size={16} />{t("Chat about this")}</button>
          <p className="caption">
            {related.basis === "citation" ? t("Cited recordings · check the original context below.")
              : related.basis === "subject" ? t("Recordings mentioning this subject · check the original context below.")
                : related.basis === "words" ? t("Recordings matching words in this point · check the original context below.")
                  : t("This point has no linked or matching recordings.")}
          </p>
          {related.sources.length ? (
            related.sources.map((source) => <RecapEvidence key={source.id} source={source} point={`${topic}\n${text}`} timezone={timezone} onCorrect={onCorrect} />)
          ) : null}
        </> : null}
      </div>
    </div>
  );
}
