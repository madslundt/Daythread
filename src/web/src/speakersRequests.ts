import { request } from "./api";
import { profileResourceUrl, profileScope } from "./profiles";

export type VoiceRecognition = { state: "disabled" | "unvalidated" | "ready"; message: string };
export type Speaker = {
  id: string; label: string; name: string | null; seconds: number;
  voice_profile_id?: string | null;
  remembered_example_id?: string | null;
  suggestion?: { profile_id: string; name: string } | null;
};
export type VoiceProfile = {
  id: string; name: string; example_count: number; conversation_count: number;
  examples: { id: string; scope: { kind: string; id: string }; speaker_id: string; seconds: number; created_at: string }[];
};
export type VoiceProfilesData = { revision: string; profiles: VoiceProfile[]; recognition: VoiceRecognition };
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
  recognition?: VoiceRecognition;
};

// Each open scope owns its transport and profile. A delayed response cannot
// restore an older version or issue a mutation against a newly selected archive.
class ScopedRequests<T> {
  private controller?: AbortController;
  protected readonly scope = profileScope();

  constructor(protected readonly path: string) {}

  cancel() { this.controller?.abort(); }

  async run(method: "GET" | "POST" | "PATCH" | "DELETE", suffix = "", body?: unknown): Promise<T | undefined> {
    this.cancel();
    const controller = new AbortController();
    this.controller = controller;
    try {
      const result = await request<T>(this.path + suffix, {
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

export class SpeakersRequests extends ScopedRequests<SpeakersData> {
  constructor(kind: "source" | "meeting", id: string) { super(`/v1/speakers/${kind}/${encodeURIComponent(id)}`); }
  audioUrl(speakerId: string, version: string): string {
    return profileResourceUrl(`${this.path}/${encodeURIComponent(speakerId)}/audio?${new URLSearchParams({ version })}`, this.scope.id);
  }
}

export class VoiceProfilesRequests extends ScopedRequests<VoiceProfilesData> {
  constructor() { super("/v1/voice-profiles"); }
  forget(profileId: string, revision: string, exampleId?: string) {
    return this.run("DELETE", `/${encodeURIComponent(profileId)}${exampleId ? `/examples/${encodeURIComponent(exampleId)}` : ""}?${new URLSearchParams({ revision })}`);
  }
}

export function speakerName(speaker: Speaker): string { return speaker.name || speaker.label; }

export function speakerTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
