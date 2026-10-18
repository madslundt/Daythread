import { t } from "./i18n";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { errorMessage } from "./api";
import {
  TranscriptTranslationRequests, translationLanguageLabel, rememberTranslationLanguage,
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

export function TranscriptTranslation({ sourceId, original, passage, content = "transcript", renderText }: {
  sourceId: string; original: string; passage?: TranslationPassage;
  content?: "transcript" | "summary";
  renderText?: (text: string, translated: boolean) => ReactNode;
}) {
  const requests = useMemo(() => new TranscriptTranslationRequests(sourceId, passage, content),
    [sourceId, passage?.start_offset, passage?.end_offset, passage?.transcript_version, content]);
  const busy = useRef(false);
  const operation = useRef(0);
  const generationTarget = useRef<string | undefined>(undefined);
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
    generationTarget.current = language;
    setGenerating(true);
    setTranslated(true);
    setError("");
    setTranslationError("");
    try {
      const result = await requests.run("POST", language);
      if (!result || currentOperation !== operation.current) return;
      setData(result);
      setTarget(language);
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
    content={content} renderText={renderText}
    selectedLanguage={generating ? generationTarget.current : target ?? data?.language} loading={loading} generating={generating}
    error={error} translationError={translationError}
    onTranslate={(language) => void translate(language)} onSelect={setTranslated}
    onLanguage={(language) => {
      rememberTranslationLanguage(language);
      setTranslated(true);
      if (language === (target ?? data?.language)) return;
      requests.cancel(); operation.current += 1;
      setLoading(true); setError(""); setTranslationError(""); setTarget(language);
    }}
    onRetry={() => setRevision((value) => value + 1)} />;
}

export function TranscriptTranslationView({ original, data, translated = false, selectedLanguage,
  loading, generating, error, translationError = "", onTranslate, onSelect, onRetry, onLanguage,
  content = "transcript", renderText }: {
  original: string;
  content?: "transcript" | "summary";
  renderText?: (text: string, translated: boolean) => ReactNode;
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
  const [languageSearch, setLanguageSearch] = useState("");
  const available = (data?.available_languages ?? []).filter((code) =>
    !data?.redundant_languages?.includes(code)
    && !(data?.already_in_target_language && code === data.language));
  const selected = selectedLanguage ?? data?.language ?? "";
  const target = available.includes(selected) ? selected : "";
  const active = translated && available.includes(target) ? target : "original";
  const language = active !== "original" ? translationLanguageLabel(active) : "";
  const translation = !loading && active === data?.language ? data.translation : null;
  const placeholder = active !== "original" && !translation;
  const tabs = ["original", ...(target ? [target] : [])];
  const analysis = data?.language_analysis;
  const recommendation = data?.recommended_language;
  const offer = !loading && active === "original" && recommendation && available.includes(recommendation)
    && analysis?.dominant_language && analysis.dominant_language_share > 0
    && analysis.dominant_language_share < 0.9
    && Object.values(analysis.language_shares).filter((share) => share > 0).length > 1;
  const matchingLanguages = available.filter((code) => `${translationLanguageLabel(code)} ${code}`.toLowerCase().includes(languageSearch.trim().toLowerCase()))
    .sort((a, b) => translationLanguageLabel(a).localeCompare(translationLanguageLabel(b)));

  function selectTab(code: string) {
    if (code === "original") onSelect(false);
    else onLanguage(code);
  }

  return <div className="transcript-translation">
    <div className="translation-tabs" role="tablist" aria-label={content === "summary" ? t("Summary language") : t("Transcript language")}
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
        {code === "original" ? t("Original") : translationLanguageLabel(code)}
      </button>)}
    </div>
    {offer ? <div className="notice">
      <span>{t("Mixed languages · {value0} is dominant (about {value1}%).", {
        value0: translationLanguageLabel(recommendation),
        value1: Math.floor(analysis.dominant_language_share * 100),
      })}</span>
      <button type="button" className="text-button" disabled={generating}
        onClick={() => onTranslate(recommendation)}>
        {t("Translate to {value0}", { value0: translationLanguageLabel(recommendation) })}
      </button>
    </div> : null}
    {available.length ? <div className="translation-language-picker">
      <label htmlFor={`${id}-language-search`}>{t("Translate to")}</label>
      <input id={`${id}-language-search`} type="search" placeholder={t("Find a language…")}
        aria-label={t("Find a translation language")} value={languageSearch} disabled={generating}
        onChange={(event) => setLanguageSearch(event.target.value)} />
      <select aria-label={t("Translation language")} value={target} disabled={generating || !available.length}
        onChange={(event) => { setLanguageSearch(""); onLanguage(event.target.value); }}>
        {!target ? <option value="" disabled>{t("Choose a language…")}</option> : null}
        {!matchingLanguages.includes(target) && target ? <option value={target}>{translationLanguageLabel(target)}</option> : null}
        {matchingLanguages.map((code) => <option key={code} value={code}>{translationLanguageLabel(code)}</option>)}
      </select>
      {!matchingLanguages.length ? <small role="status">{t("No languages found")}</small> : null}
    </div> : null}
    {loading && !placeholder ? <p className="caption" role="status">{content === "summary" ? t("Checking summary language and saved translation…") : t("Checking transcript language and saved translation…")}</p>
      : data && !data.available_languages.length ? <p className="caption">{t("Add translation languages in")}{" "}<a href="/settings/general">{t("Settings")}</a> {content === "summary" ? t("to translate this summary.") : t("to translate this transcript.")}</p>
      : translation ? <p className="caption">{content === "summary" ? t("Generated translation · The original summary is preserved.") : t("Generated translation · Corrections apply to the original transcript.")}</p> : null}
    <div id={`${id}-text`} role="tabpanel" aria-labelledby={`${id}-${active}`} tabIndex={0}>
      {placeholder ? <div className="translation-preview" aria-busy={loading || generating}>
        <p className="transcript-text translation-preview-text" aria-hidden="true">{original}</p>
        <div className="translation-preview-action">
          {loading || generating ? <p className="caption" role="status">{generating ? t("Translating…") : t("Checking saved translation…")}</p>
            : <>
              {translationError ? <p className="caption translation-error" role="alert">{translationError}</p> : null}
              <button type="button" className="translation-trigger" onClick={() => onTranslate(active)}>
                {translationError ? t("Translate again") : t("Translate to {value0}", { value0: language })}
              </button>
            </>}
        </div>
      </div> : renderText ? <div lang={translation?.language}>{renderText(translation?.text ?? original, !!translation)}</div>
        : <p className="transcript-text" lang={translation?.language}>{translation?.text ?? original}</p>}
    </div>
    {error ? <ErrorState message={error} retry={!data || loading ? onRetry : undefined} /> : null}
  </div>;
}
