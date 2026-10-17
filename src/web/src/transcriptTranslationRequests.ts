import { request } from "./api";
import { profileScope } from "./profiles";

export type TranscriptTranslationData = {
  language: string;
  available_languages: string[];
  already_in_target_language: boolean;
  translation: {
    text: string;
    language: string;
    transcript_version: string;
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

  constructor(private readonly sourceId: string, private readonly passage?: TranslationPassage) {}

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
        `/v1/transcripts/${encodeURIComponent(this.sourceId)}/translation${query}`,
        { method, signal: controller.signal }, this.scope,
      );
      return controller.signal.aborted ? undefined : result;
    } catch (error) {
      if (controller.signal.aborted || this.scope.signal.aborted) return undefined;
      throw error;
    }
  }
}

export function translationLanguageLabel(code: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "language" }).of(code) || code;
  } catch {
    return code;
  }
}
