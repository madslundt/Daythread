import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { TranscriptTranslationView } from "./TranscriptTranslation";
import { TranscriptTranslationRequests, type TranscriptTranslationData } from "./transcriptTranslationRequests";
import { selectProfile } from "./profiles";

const data: TranscriptTranslationData = {
  language: "en",
  already_in_target_language: false,
  translation: { text: "Hello, how are you?", language: "en", transcript_version: "version-1", created_at: "2026-10-16T12:00:00Z" },
};
const original = "Hej, hvordan har du det?";
const view = { original, translated: false, loading: false, generating: false, error: "",
  onTranslate: () => {}, onSelect: () => {}, onRetry: () => {} };

afterEach(() => { selectProfile("default"); vi.unstubAllGlobals(); });

describe("transcript translation requests", () => {
  it("looks up saved translations without generating and uses a separate explicit POST", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(data)));
    vi.stubGlobal("fetch", fetch);
    selectProfile("alice");
    const requests = new TranscriptTranslationRequests("source/id");
    await expect(requests.run("GET")).resolves.toEqual(data);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe("/v1/transcripts/source%2Fid/translation");
    const lookup = fetch.mock.calls[0][1] as RequestInit;
    expect(lookup.method).toBe("GET");
    expect(lookup.body).toBeUndefined();
    expect(new Headers(lookup.headers).get("X-Omi-Profile")).toBe("alice");
    await requests.run("POST");
    expect(fetch.mock.calls[1][1].method).toBe("POST");
    expect(fetch.mock.calls[1][1].body).toBeUndefined();
  });

  it("discards generation completing after a correction, close, or new lookup", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    const requests = new TranscriptTranslationRequests("source");
    const pending = requests.run("POST");
    requests.cancel();
    finish(new Response(JSON.stringify(data)));
    await expect(pending).resolves.toBeUndefined();
  });

  it("does not let a late response replace a refreshed summary language", async () => {
    let finish!: (response: Response) => void;
    const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ language: "da", already_in_target_language: false, translation: null })));
    vi.stubGlobal("fetch", fetch);
    const requests = new TranscriptTranslationRequests("source");
    const generating = requests.run("POST");
    await expect(requests.run("GET")).resolves.toEqual({ language: "da", already_in_target_language: false, translation: null });
    finish(new Response(JSON.stringify(data)));
    await expect(generating).resolves.toBeUndefined();
  });

  it("prevents a delayed component from generating in a newly selected profile", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    selectProfile("alice");
    const requests = new TranscriptTranslationRequests("source");
    selectProfile("bob");
    await expect(requests.run("POST")).resolves.toBeUndefined();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("keeps a model failure actionable for the view", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Translation model is unavailable" }), { status: 502 })));
    await expect(new TranscriptTranslationRequests("source").run("POST")).rejects.toThrow("Translation model is unavailable");
  });
});

describe("transcript translation display", () => {
  it("keeps original selected when a cached translation exists", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={data} />);
    expect(html).toContain(original);
    expect(html).not.toContain(data.translation!.text);
    expect(html).toContain('aria-pressed="true">Original');
    expect(html).toContain("English translation");
  });

  it("switches only the display to the translated language", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={data} translated />);
    expect(html).toContain(data.translation!.text);
    expect(html).not.toContain(original);
    expect(html).toContain('lang="en"');
    expect(html).toContain("Corrections apply to the original transcript");
    expect(html).toContain('aria-pressed="false">Original');
  });

  it("explains how to enable translation when the summary language is automatic", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ language: "auto", already_in_target_language: false, translation: null }} />);
    expect(html).toContain("Choose a summary language");
    expect(html).toContain('href="/settings/general"');
    expect(html).toContain('disabled=""');
    expect(html).toContain(original);
  });

  it("hides translation actions when the transcript already uses the summary language", () => {
    const sameLanguageOriginal = "We planned our next meeting.";
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} original={sameLanguageOriginal}
      data={{ ...data, already_in_target_language: true, translation: null }} />);
    expect(html).toContain(sameLanguageOriginal);
    expect(html).toContain("Transcript is already in English.");
    expect(html).not.toContain("Translate");
    expect(html).not.toContain("<button");
  });

  it("ignores a stale cached translation for a transcript already in the target language", () => {
    const sameLanguageOriginal = "We planned our next meeting.";
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} original={sameLanguageOriginal}
      data={{ ...data, already_in_target_language: true }} translated />);
    expect(html).toContain(sameLanguageOriginal);
    expect(html).not.toContain(data.translation!.text);
    expect(html).not.toContain("English translation");
    expect(html).not.toContain("<button");
    expect(html).not.toContain('lang="en"');
  });

  it("keeps translation controls hidden until the language check finishes", () => {
    for (const cached of [undefined, data]) {
      const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={cached} loading translated />);
      expect(html).toContain(original);
      expect(html).toContain("Checking transcript language");
      expect(html).not.toContain("Translate");
      expect(html).not.toContain("English translation");
      expect(html).not.toContain(data.translation!.text);
      expect(html).not.toContain("<button");
    }
  });

  it("keeps original text visible while generating and after a failure", () => {
    const pending = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, translation: null }} generating />);
    expect(pending).toContain("Translating…");
    expect(pending).toContain(original);
    expect(pending).toContain('disabled=""');
    const failure = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, translation: null }} error="Translation model is unavailable" />);
    expect(failure).toContain(original);
    expect(failure).toContain("Translation model is unavailable");
    expect(failure).toContain("Translate to English");
    expect(failure).not.toContain('disabled=""');
  });
});
