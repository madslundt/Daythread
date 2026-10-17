import { useEffect, useId, useMemo, useRef, useState } from "react";
import { errorMessage } from "./api";
import {
  TranscriptTranslationRequests, translationLanguageLabel,
  type TranscriptTranslationData, type TranslationPassage,
} from "./transcriptTranslationRequests";
import { ErrorState } from "./UI";
import type { Source } from "./types";

export function TranscriptPassage({ source, section }: {
  source: Source; section: { start_offset: number; end_offset: number };
}) {
  const original = Array.from(source.text || "").slice(section.start_offset, section.end_offset).join("");
  // Without a source version there is no safe way to translate saved offsets.
  if (!source.transcript_version) return <p className="transcript-text">{original}</p>;
  return <TranscriptTranslation key={`${source.id}:${source.transcript_version}:${section.start_offset}:${section.end_offset}`}
    sourceId={source.id} original={original} passage={{ ...section, transcript_version: source.transcript_version }} />;
}

export function TranscriptTranslation({ sourceId, original, passage }: {
  sourceId: string; original: string; passage?: TranslationPassage;
}) {
  const requests = useMemo(() => new TranscriptTranslationRequests(sourceId, passage),
    [sourceId, passage?.start_offset, passage?.end_offset, passage?.transcript_version]);
  const busy = useRef(false);
  const operation = useRef(0);
  const [target, setTarget] = useState<string>();
  const [data, setData] = useState<TranscriptTranslationData>();
  const [translated, setTranslated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const [translationError, setTranslationError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    operation.current += 1;
    busy.current = false;
    setLoading(true);
    setGenerating(false);
    setError("");
    requests.run("GET", target).then((result) => {
      if (active && result) { setData(result); setLoading(false); }
    }).catch((failure) => {
      if (active) {
        setData((current) => current ? { ...current, translation: null } : undefined);
        setError(errorMessage(failure)); setLoading(false);
      }
    });
    return () => { active = false; requests.cancel(); };
  }, [requests, revision, target]);

  useEffect(() => {
    setData(undefined); setTarget(undefined); setTranslated(false); setTranslationError("");
  }, [requests]);

  useEffect(() => {
    const refresh = () => {
      requests.cancel();
      operation.current += 1;
      setData(undefined);
      setTarget(undefined);
      setTranslated(false);
      setTranslationError("");
      setRevision((value) => value + 1);
    };
    const focus = () => {
      // Returning to the tab must not cancel an explicit Translate click.
      if (!busy.current) setRevision((value) => value + 1);
    };
    window.addEventListener("focus", focus);
    window.addEventListener("omi:settings-changed", refresh);
    window.addEventListener("omi:archive-changed", refresh);
    return () => {
      window.removeEventListener("focus", focus);
      window.removeEventListener("omi:settings-changed", refresh);
      window.removeEventListener("omi:archive-changed", refresh);
    };
  }, [requests]);

  async function translate(language: string) {
    if (loading || busy.current || !data?.available_languages.includes(language)
      || data.redundant_languages?.includes(language)) return;
    const currentOperation = ++operation.current;
    busy.current = true;
    setGenerating(true);
    setTranslated(true);
    setError("");
    setTranslationError("");
    try {
      const result = await requests.run("POST", language);
      if (!result || currentOperation !== operation.current) return;
      setData(result);
      if (result.already_in_target_language) setTranslated(false);
    } catch (failure) {
      if (currentOperation === operation.current) setTranslationError(errorMessage(failure));
    } finally {
      if (currentOperation === operation.current) {
        busy.current = false;
        setGenerating(false);
      }
    }
  }

  return <TranscriptTranslationView original={original} data={data} translated={translated}
    selectedLanguage={target ?? data?.language} loading={loading} generating={generating}
    error={error} translationError={translationError}
    onTranslate={(language) => void translate(language)} onSelect={setTranslated}
    onLanguage={(language) => {
      setTranslated(true);
      if (language === (target ?? data?.language)) return;
      requests.cancel(); operation.current += 1;
      setLoading(true); setError(""); setTranslationError(""); setTarget(language);
    }}
    onRetry={() => setRevision((value) => value + 1)} />;
}

export function TranscriptTranslationView({ original, data, translated = false, selectedLanguage,
  loading, generating, error, translationError = "", onTranslate, onSelect, onRetry, onLanguage }: {
  original: string;
  data?: TranscriptTranslationData;
  translated?: boolean;
  selectedLanguage?: string;
  loading: boolean;
  generating: boolean;
  error: string;
  translationError?: string;
  onTranslate: (language: string) => void;
  onSelect: (translated: boolean) => void;
  onRetry: () => void;
  onLanguage: (language: string) => void;
}) {
  const id = useId();
  const available = (data?.available_languages ?? []).filter((code) =>
    !data?.redundant_languages?.includes(code)
    && !(data?.already_in_target_language && code === data.language));
  const target = selectedLanguage ?? data?.language ?? "";
  const active = translated && available.includes(target) ? target : "original";
  const language = active !== "original" ? translationLanguageLabel(active) : "";
  const translation = !loading && active === data?.language ? data.translation : null;
  const placeholder = active !== "original" && !translation;
  const tabs = ["original", ...available];

  function selectTab(code: string) {
    if (code === "original") onSelect(false);
    else onLanguage(code);
  }

  return <div className="transcript-translation">
    <div className="translation-tabs" role="tablist" aria-label="Transcript language"
      onKeyDown={(event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        // Use manual tab activation so arrow navigation never starts a lookup.
        const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("[role=tab]:not(:disabled)"));
        const index = buttons.indexOf(event.target as HTMLButtonElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
        buttons[next]?.focus();
      }}>
      {tabs.map((code) => <button key={code} type="button" role="tab" id={`${id}-${code}`}
        aria-controls={`${id}-text`} aria-selected={active === code} tabIndex={active === code ? 0 : -1}
        disabled={generating && code !== "original" && code !== target}
        onClick={() => selectTab(code)}>
        {code === "original" ? "Original" : translationLanguageLabel(code)}
      </button>)}
    </div>
    {loading && !placeholder ? <p className="caption" role="status">Checking transcript language and saved translation…</p>
      : data && !data.available_languages.length ? <p className="caption">Add translation languages in <a href="/settings/general">Settings</a> to translate this transcript.</p>
      : translation ? <p className="caption">Generated translation · Corrections apply to the original transcript.</p> : null}
    <div id={`${id}-text`} role="tabpanel" aria-labelledby={`${id}-${active}`} tabIndex={0}>
      {placeholder ? <div className="translation-preview" aria-busy={loading || generating}>
        <p className="transcript-text translation-preview-text" aria-hidden="true">{original}</p>
        <div className="translation-preview-action">
          {loading || generating ? <p className="caption" role="status">{generating ? "Translating…" : "Checking saved translation…"}</p>
            : <>
              {translationError ? <p className="caption translation-error" role="alert">{translationError}</p> : null}
              <button type="button" className="translation-trigger" onClick={() => onTranslate(active)}>
                {translationError ? "Translate again" : `Translate to ${language}`}
              </button>
            </>}
        </div>
      </div> : <p className="transcript-text" lang={translation?.language}>{translation?.text ?? original}</p>}
    </div>
    {error ? <ErrorState message={error} retry={!data || loading ? onRetry : undefined} /> : null}
  </div>;
}
