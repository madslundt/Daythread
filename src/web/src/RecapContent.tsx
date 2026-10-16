import { ImportanceButton, type ImportanceTarget } from "./Importance";
import { useId, useState } from "react";
import { Icon } from "./Icon";
import { itemSources, recapParts } from "./recapItems";
import { SourceList } from "./SourceList";
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

export function RecapContent({ text, day, version, sources, timezone, onChat, onCorrect }: {
  text: string;
  day: string;
  version: string;
  sources: Source[];
  timezone: string;
  onChat: (scope: ChatScope, question?: string) => void;
  onCorrect: () => void;
}) {
  const storageKey = `omi.recap-checks.v1:${day}:${version}`;
  const [checks, setChecks] = useState(() => savedChecks(storageKey));
  const [storageError, setStorageError] = useState(false);
  const parts = recapParts(text);
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
  return (
    <div className="prose recap-items">
      {savedVersion ? <ImportanceButton target={target()} label="day recap" /> : null}
      <p className="caption recap-item-hint">Open any point for recordings and a conversation.</p>
      {parts.map((part, index) => part.kind === "heading" ? (
        <div key={index} className="recap-section-heading"><h3><InlineText text={part.text} /></h3>{savedVersion ? <ImportanceButton target={target(part.text)} label={part.text} /> : null}</div>
      ) : part.kind === "text" ? (
        <div key={index}><RichText text={part.text} />{savedVersion ? <ImportanceButton target={target(part.text)} label="recap section" /> : null}</div>
      ) : (
        <RecapItem
          key={`${index}:${part.text}`}
          text={part.text}
          importanceTarget={savedVersion ? target(part.text) : undefined}
          action={part.action}
          checked={checks.includes(part.text)}
          onToggle={() => toggle(part.text)}
          sources={sources}
          timezone={timezone}
          onChat={() => onChat({ kind: "day", day }, `Tell me more about this point from my day recap: ${part.text}`)}
          onCorrect={onCorrect}
        />
      ))}
      {parts.some((part) => part.kind === "item" && part.action) ? (
        <p className="caption recap-check-hint" role={storageError ? "status" : undefined}>
          {storageError ? "Checks are kept for this visit only because browser storage is unavailable." : "Optional checkmarks · saved in this browser. Inbox status is managed separately."}
        </p>
      ) : null}
    </div>
  );
}

function RecapItem({ text, importanceTarget, action, checked, onToggle, sources, timezone, onChat, onCorrect }: {
  text: string;
  importanceTarget?: ImportanceTarget;
  action: boolean;
  checked: boolean;
  onToggle: () => void;
  sources: Source[];
  timezone: string;
  onChat: () => void;
  onCorrect: () => void;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const related = itemSources(text, sources);
  return (
    <div className={`recap-item ${action ? "recap-action" : ""} ${checked ? "is-checked" : ""}`}>
      <div className="recap-item-line">
        {action ? (
          <label className="recap-check-target">
            <input type="checkbox" checked={checked} onChange={onToggle} aria-label={`Mark complete: ${text.replace(/\*\*/g, "")}`} />
          </label>
        ) : <span className="recap-bullet" aria-hidden="true">•</span>}
        <button type="button" className="recap-item-button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((value) => !value)}>
          <span><InlineText text={text} /></span>
          <Icon name="chevron-right" size={16} />
        </button>
      </div>
      {importanceTarget ? <ImportanceButton target={importanceTarget} label="recap point" /> : null}
      <div id={id} hidden={!open} className="recap-item-detail">
        {open ? <>
          <button type="button" className="button small" onClick={onChat}><Icon name="chat" size={16} />Chat about this</button>
          <p className="caption">
            {related.basis === "citation" ? "Cited recordings · check the original context below."
              : related.basis === "subject" ? "Recordings mentioning this subject · check the original context below."
                : related.basis === "words" ? "Recordings matching words in this point · check the original context below."
                  : "This point has no linked or matching recordings."}
          </p>
          {related.sources.length ? (
            <SourceList sources={related.sources} timezone={timezone} expanded onCorrect={onCorrect} idPrefix={`recap-${id}`} />
          ) : null}
        </> : null}
      </div>
    </div>
  );
}
