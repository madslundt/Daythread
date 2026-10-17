import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { TranscriptTranslationView } from "./TranscriptTranslation";
import { TranscriptTranslationRequests, type TranscriptTranslationData } from "./transcriptTranslationRequests";
import { selectProfile } from "./profiles";

const data: TranscriptTranslationData = {
  language: "en",
  available_languages: ["en", "da", "de"],
  already_in_target_language: false,
  translation: { text: "Hello, how are you?", language: "en", transcript_version: "version-1", created_at: "2026-10-16T12:00:00Z" },
};
const original = "Hej, hvordan har du det?";
const view = { original, translated: false, loading: false, generating: false, error: "",
  onTranslate: () => {}, onSelect: () => {}, onRetry: () => {}, onLanguage: () => {} };

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

  it("scopes topic translation to the same versioned Unicode passage for lookup and generation", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(data)));
    vi.stubGlobal("fetch", fetch);
    const requests = new TranscriptTranslationRequests("source", {
      start_offset: 27, end_offset: 108, transcript_version: "version+1",
    });
    await requests.run("GET");
    await requests.run("POST");
    for (const [url] of fetch.mock.calls) {
      expect(url).toBe("/v1/transcripts/source/translation?start_offset=27&end_offset=108&transcript_version=version%2B1");
    }
  });

  it("sends the selected target alongside versioned passage bounds for both methods", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(data)));
    vi.stubGlobal("fetch", fetch);
    const requests = new TranscriptTranslationRequests("source", {
      start_offset: 2, end_offset: 20, transcript_version: "version-1",
    });
    await requests.run("GET", "de");
    await requests.run("POST", "de");
    for (const [url] of fetch.mock.calls) {
      const query = new URL(url, "http://localhost").searchParams;
      expect(query.get("language")).toBe("de");
      expect(query.get("start_offset")).toBe("2");
      expect(query.get("end_offset")).toBe("20");
      expect(query.get("transcript_version")).toBe("version-1");
    }
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

  it("does not let a late response replace a newly selected translation language", async () => {
    let finish!: (response: Response) => void;
    const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ language: "da", already_in_target_language: false, translation: null })));
    vi.stubGlobal("fetch", fetch);
    const requests = new TranscriptTranslationRequests("source");
    const generating = requests.run("POST", "en");
    await expect(requests.run("GET", "da")).resolves.toEqual({ language: "da", already_in_target_language: false, translation: null });
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
  it("shows Original and every eligible language as tabs before translation exists", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, translation: null }} />);
    expect(html).toContain('role="tablist"');
    for (const label of ["Original", "English", "Danish", "German"]) expect(html).toContain(`>${label}</button>`);
    expect(html).not.toContain("<select");
    expect(html).not.toContain("<option");
    expect(html).not.toContain("translation-trigger");
    expect(html).toContain(original);
  });

  it("selecting an untranslated language shows blurred original and an explicit action", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, translation: null }} translated />);
    expect(html).toContain('class="transcript-text translation-preview-text" aria-hidden="true"');
    expect(html).toContain(original);
    expect(html).toContain("Translate to English");
    expect(html).toContain('aria-selected="true" tabindex="0">English');
    expect(html).toContain('role="tabpanel"');
  });

  it("shows saved translated text with the correct language and selected tab", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={data} translated />);
    expect(html).toContain(data.translation!.text);
    expect(html).not.toContain(original);
    expect(html).toContain('aria-selected="true" tabindex="0">English');
    expect(html).toContain('lang="en"');
    expect(html).toContain("Corrections apply to the original transcript");
    expect(html).not.toContain("translation-preview");
  });

  it("shows the original when selected even if a translation is cached", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={data} />);
    expect(html).toContain(original);
    expect(html).not.toContain(data.translation!.text);
    expect(html).toContain('aria-selected="true" tabindex="0">Original');
    expect(html).not.toContain("translation-preview");
  });

  it("hides all redundant target tabs while keeping other configured languages", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, redundant_languages: ["en", "da"] }} />);
    expect(html).not.toContain(">English</button>");
    expect(html).not.toContain(">Danish</button>");
    expect(html).toContain(">German</button>");
    expect(html).toContain(">Original</button>");
  });

  it("does not offer translation to the current matching target, including stale caches", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, already_in_target_language: true }} translated />);
    expect(html).toContain(original);
    expect(html).not.toContain(data.translation!.text);
    expect(html).not.toContain(">English</button>");
    expect(html).not.toContain("Translate to English");
  });

  it("explains how to enable translation when none are configured", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ language: "", available_languages: [], already_in_target_language: false, translation: null }} />);
    expect(html).toContain("Add translation languages");
    expect(html).toContain('href="/settings/general"');
    expect(html).toContain(original);
    expect(html).not.toContain("translation-trigger");
  });

  it("shows a blurred original with status during lookup and generation", () => {
    for (const state of [{ loading: true }, { generating: true }]) {
      const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, translation: null }} translated {...state} />);
      expect(html).toContain("translation-preview-text");
      expect(html).toContain(original);
      expect(html).toContain('aria-busy="true"');
      expect(html).toContain('role="status"');
      expect(html).toContain(state.loading ? "Checking saved translation…" : "Translating…");
      expect(html).not.toContain("translation-trigger");
    }
  });

  it("does not display another language's cache while looking up a selected tab", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={data} translated selectedLanguage="de" loading />);
    expect(html).not.toContain(data.translation!.text);
    expect(html).toContain('aria-selected="true" tabindex="0">German');
    expect(html).toContain("translation-preview-text");
  });

  it("allows returning to the original during generation", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, translation: null }} generating />);
    expect(html).toContain(original);
    expect(html).not.toContain("translation-preview-text");
    expect(html).toContain('aria-selected="true" tabindex="0">Original');
  });

  it("shows the failure and retry action over the blurred original", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, translation: null }} translated translationError="Translation model is unavailable" />);
    expect(html).toContain("Translation model is unavailable");
    expect(html).toContain("translation-preview-text");
    expect(html).toContain('class="caption translation-error" role="alert"');
    expect(html).toContain("Translate again");
    expect(html).not.toContain('disabled=""');
  });

  it("keeps a translation failure out of the Original tab", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, translation: null }} translationError="Translation model is unavailable" />);
    expect(html).toContain(original);
    expect(html).not.toContain("Translation model is unavailable");
    expect(html).not.toContain("translation-preview-text");
  });
});
