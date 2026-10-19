import { useState } from "react";
import { errorMessage, request } from "./api";
import { t } from "./i18n";
import { statusLabel } from "./statusLabels";
import { ErrorState } from "./UI";
import { useResource } from "./useResource";

export type EmbeddingProgress = {
  state: string; indexed: number; total: number; worker_running?: boolean;
};

export function embeddingProgressState(progress: EmbeddingProgress): string {
  if (["disabled", "unavailable"].includes(progress.state)) return progress.state;
  return progress.indexed < progress.total ? "incomplete" : progress.state;
}

export function EmbeddingStatus({ profileSelected }: { profileSelected: boolean }) {
  const resource = useResource<{ embeddings: EmbeddingProgress }>(profileSelected ? "/v1/status" : null, 10000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const progress = resource.data?.embeddings;
  async function rebuild() {
    if (busy || !progress || progress.state === "disabled") return;
    setBusy(true); setError("");
    try {
      await request<EmbeddingProgress>("/v1/embeddings/rebuild", { method: "POST", body: "{}" });
      resource.refresh();
    } catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }
  return <section className="model-connection embedding-status" aria-label={t("Search index")}>
    <h3>{t("Search index")}</h3>
    {!profileSelected ? <p className="caption">{t("Choose a profile in Settings → Profiles first.")}</p> : null}
    {progress ? <>
      <p role="status">{embeddingProgressState(progress) === "incomplete" ? t("Index incomplete") : statusLabel(progress.state)}
        {" · "}{t("{indexed} of {total} recordings indexed", { indexed: progress.indexed, total: progress.total })}</p>
      <progress max={Math.max(1, progress.total)} value={progress.indexed}
        aria-label={t("Search index")} />
      <p className="caption">{t("Keyword search remains available while indexing or when the embedding service is unavailable.")}</p>
      <p className="caption">{progress.worker_running ? t("Background indexing is running.") : t("Background indexing is not running.")}</p>
      <button type="button" className="button" disabled={busy || progress.state === "disabled"} onClick={rebuild}>
        {busy ? t("Rebuilding…") : t("Rebuild search index")}</button>
    </> : null}
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {error ? <ErrorState message={error} /> : null}
  </section>;
}
