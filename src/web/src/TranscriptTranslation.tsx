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
  const [translated, setTranslated] = useState(true);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    operation.current += 1;
    setData(undefined);
    busy.current = false;
    setLoading(true);
    setGenerating(false);
    setError("");
    requests.run("GET", target).then((result) => {
      if (active && result) { setData(result); setLoading(false); }
    }).catch((failure) => {
      if (active) { setError(errorMessage(failure)); setLoading(false); }
    });
    return () => { active = false; requests.cancel(); };
  }, [requests, revision, target]);

  useEffect(() => { setTranslated(true); }, [requests]);

  useEffect(() => {
    const refresh = () => {
      requests.cancel();
      operation.current += 1;
      setData(undefined);
      setTarget(undefined);
      setTranslated(true);
      setRevision((value) => value + 1);
    };
    const focus = () => {
      // Returning to the tab must not cancel an explicit Translate click.
      if (!busy.current) { setTarget(undefined); setRevision((value) => value + 1); }
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

  async function translate() {
    if (loading || generating || !data?.available_languages.includes(data.language)) return;
    const currentOperation = ++operation.current;
    busy.current = true;
    setGenerating(true);
    setError("");
    try {
      const result = await requests.run("POST", data.language);
      if (!result || currentOperation !== operation.current) return;
      setData(result);
      setTranslated(Boolean(result.translation) && !result.already_in_target_language);
    } catch (failure) {
      if (currentOperation === operation.current) setError(errorMessage(failure));
    } finally {
      if (currentOperation === operation.current) {
        busy.current = false;
        setGenerating(false);
      }
    }
  }

  return <TranscriptTranslationView original={original} data={data} translated={translated}
    loading={loading} generating={generating} error={error}
    onTranslate={() => void translate()} onSelect={setTranslated}
    onLanguage={(language) => {
      requests.cancel(); operation.current += 1;
      setLoading(true); setTranslated(true); setError(""); setTarget(language);
    }}
    onRetry={() => setRevision((value) => value + 1)} />;
}

export function TranscriptTranslationView({ original, data, translated = true, loading, generating, error,
  onTranslate, onSelect, onRetry, onLanguage }: {
  original: string;
  data?: TranscriptTranslationData;
  translated?: boolean;
  loading: boolean;
  generating: boolean;
  error: string;
  onTranslate: () => void;
  onSelect: (translated: boolean) => void;
  onRetry: () => void;
  onLanguage: (language: string) => void;
}) {
  const selectId = useId();
  const alreadyInTargetLanguage = !loading && data?.already_in_target_language;
  const translation = !loading && !alreadyInTargetLanguage ? data?.translation : null;
  const language = data?.language ? translationLanguageLabel(data.language) : "";
  const available = data?.available_languages ?? [];
  return <div>
    <div className="button-row" aria-label="Transcript language">
      {available.length ? <>
        <label htmlFor={selectId}>Translate to</label>
        <select id={selectId} value={data?.language ?? ""} disabled={loading || generating}
          onChange={(event) => onLanguage(event.target.value)}>
          {available.map((code) => <option key={code} value={code}>{translationLanguageLabel(code)}</option>)}
        </select>
      </> : null}
      <button type="button" className="button small" onClick={onTranslate}
        disabled={loading || generating || !available.length}>
        {generating ? "Translating…" : language ? `Translate to ${language}` : "Translate"}
      </button>
      {translation ? <>
        <button type="button" className="button small" aria-pressed={!translated} onClick={() => onSelect(false)}>Original</button>
        <button type="button" className="button small" aria-pressed={translated} onClick={() => onSelect(true)}>{language} translation</button>
      </> : null}
    </div>
    {loading ? <p className="caption" role="status">Checking transcript language and saved translation…</p>
      : data && !available.length ? <p className="caption">Add translation languages in <a href="/settings/general">Settings</a> to translate this transcript.</p>
      : alreadyInTargetLanguage ? <p className="caption" role="status">Transcript is already in {language}. You can choose another translation language.</p>
      : <p className="caption" role="status">{generating ? "Translating. Your original transcript is kept." : translated && translation ? `Showing a generated ${language} translation. Corrections apply to the original transcript.` : translation ? `Showing the original transcript. A ${language} translation is available.` : `Original transcript · languages detected automatically.${data ? " Translation is generated only when you click Translate." : ""}`}</p>}
    <p className="transcript-text" lang={translated && translation ? translation.language : undefined}>
      {translated && translation ? translation.text : original}
    </p>
    {error ? <ErrorState message={error} retry={data ? undefined : onRetry} /> : null}
  </div>;
}
