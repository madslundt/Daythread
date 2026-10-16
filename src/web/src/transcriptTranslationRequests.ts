import { request } from "./api";
import { profileScope } from "./profiles";

export type TranscriptTranslationData = {
  language: string;
  translation: {
    text: string;
    language: string;
    transcript_version: string;
    created_at: string;
  } | null;
};

// One expanded transcript owns its requests. Closing, correcting, or refreshing it
// cancels pending work, including transports that finish after cancellation.
export class TranscriptTranslationRequests {
  private controller?: AbortController;
  private readonly scope = profileScope();

  constructor(private readonly sourceId: string) {}

  cancel() {
    this.controller?.abort();
  }

  async run(method: "GET" | "POST"): Promise<TranscriptTranslationData | undefined> {
    this.cancel();
    const controller = new AbortController();
    this.controller = controller;
    try {
      const result = await request<TranscriptTranslationData>(
        `/v1/transcripts/${encodeURIComponent(this.sourceId)}/translation`,
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
