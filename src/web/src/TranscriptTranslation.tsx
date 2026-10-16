import { useEffect, useState } from "react";
import { errorMessage } from "./api";
import {
  TranscriptTranslationRequests, translationLanguageLabel,
  type TranscriptTranslationData,
} from "./transcriptTranslationRequests";
import { ErrorState } from "./UI";

export function TranscriptTranslation({ sourceId, original }: { sourceId: string; original: string }) {
  const [requests] = useState(() => new TranscriptTranslationRequests(sourceId));
  const [data, setData] = useState<TranscriptTranslationData>();
  const [translated, setTranslated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setData(undefined);
    setTranslated(false);
    setLoading(true);
    setGenerating(false);
    setError("");
    requests.run("GET").then((result) => {
      if (active && result) { setData(result); setLoading(false); }
    }).catch((failure) => {
      if (active) { setError(errorMessage(failure)); setLoading(false); }
    });
    return () => { active = false; requests.cancel(); };
  }, [requests, revision]);

  useEffect(() => {
    const refresh = () => {
      requests.cancel();
      setData(undefined);
      setTranslated(false);
      setRevision((value) => value + 1);
    };
    window.addEventListener("focus", refresh);
    window.addEventListener("omi:settings-changed", refresh);
    window.addEventListener("omi:archive-changed", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      window.removeEventListener("omi:settings-changed", refresh);
      window.removeEventListener("omi:archive-changed", refresh);
    };
  }, [requests]);

  async function translate() {
    if (loading || generating || !data || data.language === "auto") return;
    setGenerating(true);
    setError("");
    try {
      const result = await requests.run("POST");
      if (!result) return;
      setData(result);
      setTranslated(Boolean(result.translation));
      setGenerating(false);
    } catch (failure) {
      setError(errorMessage(failure));
      setGenerating(false);
    }
  }

  return <TranscriptTranslationView original={original} data={data} translated={translated}
    loading={loading} generating={generating} error={error}
    onTranslate={() => void translate()} onSelect={setTranslated}
    onRetry={() => setRevision((value) => value + 1)} />;
}

export function TranscriptTranslationView({ original, data, translated, loading, generating, error,
  onTranslate, onSelect, onRetry }: {
  original: string;
  data?: TranscriptTranslationData;
  translated: boolean;
  loading: boolean;
  generating: boolean;
  error: string;
  onTranslate: () => void;
  onSelect: (translated: boolean) => void;
  onRetry: () => void;
}) {
  const translation = data?.translation;
  const language = data && data.language !== "auto" ? translationLanguageLabel(data.language) : "";
  return <div>
    <div className="button-row" aria-label="Transcript language">
      {translation ? <>
        <button type="button" className="button small" aria-pressed={!translated} onClick={() => onSelect(false)}>Original</button>
        <button type="button" className="button small" aria-pressed={translated} onClick={() => onSelect(true)}>{language} translation</button>
      </> : <button type="button" className="button small" onClick={onTranslate}
        disabled={loading || generating || !data || data.language === "auto"}>
        {generating ? "Translating…" : language ? `Translate to ${language}` : "Translate to summary language"}
      </button>}
    </div>
    {data?.language === "auto" ? <p className="caption">Choose a summary language in <a href="#settings/general">Settings</a> to translate this transcript.</p>
      : <p className="caption" role="status">{loading ? "Checking for a saved translation…" : generating ? "Translating. Your original transcript is kept." : translated && translation ? "Showing a generated translation. Corrections apply to the original transcript." : "Original transcript · languages detected automatically. Translation is generated only when you click Translate."}</p>}
    <p className="transcript-text" lang={translated && translation ? translation.language : undefined}>
      {translated && translation ? translation.text : original}
    </p>
    {error ? <ErrorState message={error} retry={data ? undefined : onRetry} /> : null}
  </div>;
}
