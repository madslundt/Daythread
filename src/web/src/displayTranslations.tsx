import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { errorMessage, request } from "./api";
import { t } from "./i18n";
import { profileScope } from "./profiles";

export type DisplayTranslationRef = { kind: "inbox" | "decision"; id: string; version: number }
  | { kind: "source_topic"; id: string; source_version: string; transcript_version: string;
      start_offset: number; end_offset: number; chapter_id?: string };
export type DisplayTranslationInput = DisplayTranslationRef & { original_title: string };
export type DisplayTranslationItem = DisplayTranslationInput & { title: string; content_key: string };
export const DisplayTranslationLanguage = createContext<string | undefined>(undefined);

function reference(item: DisplayTranslationRef): DisplayTranslationRef {
  return item.kind === "source_topic" ? { kind: item.kind, id: item.id, source_version: item.source_version,
    transcript_version: item.transcript_version, start_offset: item.start_offset, end_offset: item.end_offset,
    ...(item.chapter_id ? { chapter_id: item.chapter_id } : {}) }
    : { kind: item.kind, id: item.id, version: item.version };
}
export function displayTranslationKey(item: DisplayTranslationInput): string {
  return JSON.stringify([reference(item), item.original_title]);
}

// The server resolves prose from canonical references. No translated record is sent to a write API.
export class DisplayTranslationRequests {
  private readonly scope = profileScope();
  private controller?: AbortController;
  cancel() { this.controller?.abort(); }
  async run(language: string, inputs: DisplayTranslationInput[]): Promise<DisplayTranslationItem[] | undefined> {
    this.cancel();
    if (this.scope.signal.aborted) return;
    const controller = new AbortController(); this.controller = controller;
    const result: DisplayTranslationItem[] = [];
    try {
      for (let offset = 0; offset < inputs.length; offset += 50) {
        const batch = inputs.slice(offset, offset + 50);
        const response = await request<{ language: string; items: DisplayTranslationItem[] }>("/v1/display-translations", {
          method: "POST", signal: controller.signal,
          body: JSON.stringify({ language, items: batch.map(reference) }),
        }, this.scope);
        if (controller.signal.aborted || this.scope.signal.aborted) return;
        const expected = new Set(batch.map(displayTranslationKey));
        if (!response || response.language !== language || !Array.isArray(response.items) || response.items.length !== batch.length
          || response.items.some((item) => !item || !expected.delete(displayTranslationKey(item))
            || typeof item.title !== "string" || !item.title.trim() || !item.content_key)) {
          throw new Error(t("The overview translation could not be loaded. Try again."));
        }
        result.push(...response.items);
      }
      return result;
    } catch (error) {
      if (controller.signal.aborted || this.scope.signal.aborted) return;
      throw error;
    }
  }
}

export function useDisplayTranslations(inputs: DisplayTranslationInput[], scopeKey = "") {
  const language = useContext(DisplayTranslationLanguage);
  const scope = profileScope();
  const serialized = JSON.stringify(inputs);
  const stableInputs = useMemo(() => inputs, [serialized]);
  const [revision, setRevision] = useState(0);
  const key = JSON.stringify([scope.id, scopeKey, language, serialized, revision]);
  const [loaded, setLoaded] = useState<{ key: string; items: DisplayTranslationItem[]; error: string }>();
  useEffect(() => {
    const invalidate = () => { setLoaded(undefined); setRevision((value) => value + 1); };
    window.addEventListener("omi:archive-changed", invalidate);
    window.addEventListener("omi:settings-changed", invalidate);
    return () => {
      window.removeEventListener("omi:archive-changed", invalidate);
      window.removeEventListener("omi:settings-changed", invalidate);
    };
  }, []);
  useEffect(() => {
    if (!language || !stableInputs.length) return;
    const client = new DisplayTranslationRequests();
    void client.run(language, stableInputs).then((items) => {
      if (items) setLoaded({ key, items, error: "" });
    }).catch((error) => setLoaded({ key, items: [], error: errorMessage(error) }));
    return () => client.cancel();
  }, [key, language, stableInputs, revision]);
  const current = language && loaded?.key === key ? loaded : undefined;
  const titles = useMemo(() => new Map(current?.items.map((item) => [displayTranslationKey(item), item.title])), [current?.items]);
  const title = useCallback((item: DisplayTranslationInput) => titles.get(displayTranslationKey(item)) || item.original_title, [titles]);
  return {
    language,
    title,
    loading: Boolean(language && inputs.length && !current), error: current?.error || "",
    retry: () => { setLoaded(undefined); setRevision((value) => value + 1); },
  };
}

export function DisplayTranslationStatus({ value }: { value: Pick<ReturnType<typeof useDisplayTranslations>, "loading" | "error" | "retry"> }) {
  return value.loading ? <p className="caption" role="status">{t("Translating…")}</p>
    : value.error ? <p className="caption translation-error" role="alert">{value.error}{" "}
      <button className="text-button" onClick={value.retry}>{t("Translate again")}</button></p> : null;
}
