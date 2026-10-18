import { languageCodes } from "./languages";
import { request } from "./api";
import { profileScope } from "./profiles";
import { interfaceLanguage } from "./i18n";

export type TranscriptTranslationData = {
  language: string;
  available_languages: string[];
  redundant_languages?: string[];
  language_analysis?: {
    dominant_language: string | null;
    dominant_language_share: number;
    language_shares: Record<string, number>;
  };
  recommended_language?: string | null;
  already_in_target_language: boolean;
  translation: {
    text: string;
    language: string;
    transcript_version?: string;
    summary_version?: string;
    created_at: string;
  } | null;
};

export type TranslationPassage = {
  start_offset: number;
  end_offset: number;
  transcript_version: string;
};

// One expanded transcript owns its requests. Closing, correcting, or refreshing it
// cancels pending work, including transports that finish after cancellation.
export class TranscriptTranslationRequests {
  private controller?: AbortController;
  private readonly scope = profileScope();

  constructor(private readonly sourceId: string, private readonly passage?: TranslationPassage,
    private readonly content: "transcript" | "summary" = "transcript") {}

  cancel() {
    this.controller?.abort();
  }

  async run(method: "GET" | "POST", language?: string): Promise<TranscriptTranslationData | undefined> {
    this.cancel();
    const controller = new AbortController();
    this.controller = controller;
    try {
      const parameters = new URLSearchParams(this.passage ? {
        start_offset: String(this.passage.start_offset),
        end_offset: String(this.passage.end_offset),
        transcript_version: this.passage.transcript_version,
      } : {});
      if (language) parameters.set("language", language);
      const query = parameters.size ? `?${parameters}` : "";
      const result = await request<TranscriptTranslationData>(
        `/v1/${this.content === "summary" ? "outputs" : "transcripts"}/${encodeURIComponent(this.sourceId)}/translation${query}`,
        { method, signal: controller.signal }, this.scope,
      );
      return controller.signal.aborted ? undefined : result;
    } catch (error) {
      if (controller.signal.aborted || this.scope.signal.aborted) return undefined;
      throw error;
    }
  }
}

const languageLabels = new Map<string, string>();
export function translationLanguageLabel(code: string): string {
  const locale = interfaceLanguage();
  const key = `${locale}:${code}`;
  const cached = languageLabels.get(key);
  if (cached) return cached;
  let label = code;
  try { label = new Intl.DisplayNames([locale], { type: "language" }).of(code) || code; } catch { /* Preserve unknown language codes. */ }
  languageLabels.set(key, label);
  return label;
}

const translationTargetKey = "daythread:translation-language:v1";

export function preferredTranslationLanguage(): string {
  try {
    const saved = localStorage.getItem(translationTargetKey);
    if (saved && languageCodes.includes(saved)) return saved;
  } catch { /* Translation works when browser storage is unavailable. */ }
  return languageCodes.includes(interfaceLanguage()) ? interfaceLanguage() : "en";
}

export function rememberTranslationLanguage(code: string) {
  if (!languageCodes.includes(code)) return;
  try { localStorage.setItem(translationTargetKey, code); } catch { /* Keep the choice for this visit. */ }
}
