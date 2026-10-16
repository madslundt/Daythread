import { request } from "./api";
import { profileResourceUrl, profileScope } from "./profiles";

export type Speaker = { id: string; label: string; name: string | null; seconds: number };
export type SpeakerTurn = { id: string; source_id: string; start: number; end: number; speaker_id: string; text?: string };
export type SpeakersData = {
  state: "idle" | "ready" | "stale";
  configured: boolean;
  version: string | null;
  speakers: Speaker[];
  turns: SpeakerTurn[];
  audio_available: boolean;
  scope?: { kind: "source" | "meeting"; id: string };
  source_count?: number;
};

// Each open scope owns its transport and profile. A delayed response cannot
// restore an older version or issue a mutation against a newly selected archive.
export class SpeakersRequests {
  private controller?: AbortController;
  private readonly scope = profileScope();
  private readonly path: string;

  constructor(kind: "source" | "meeting", id: string) {
    this.path = `/v1/speakers/${kind}/${encodeURIComponent(id)}`;
  }

  cancel() { this.controller?.abort(); }

  audioUrl(speakerId: string, version: string): string {
    return profileResourceUrl(`${this.path}/${encodeURIComponent(speakerId)}/audio?${new URLSearchParams({ version })}`, this.scope.id);
  }

  async run(method: "GET" | "POST" | "PATCH", suffix = "", body?: unknown): Promise<SpeakersData | undefined> {
    this.cancel();
    const controller = new AbortController();
    this.controller = controller;
    try {
      const result = await request<SpeakersData>(this.path + suffix, {
        method, signal: controller.signal,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      }, this.scope);
      return controller.signal.aborted ? undefined : result;
    } catch (error) {
      if (controller.signal.aborted || this.scope.signal.aborted) return undefined;
      throw error;
    }
  }
}

export function speakerName(speaker: Speaker): string { return speaker.name || speaker.label; }

export function speakerTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
