import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { TranscriptTranslationView } from "./TranscriptTranslation";
import { TranscriptTranslationRequests, preferredTranslationLanguage, rememberTranslationLanguage, type TranscriptTranslationData } from "./transcriptTranslationRequests";
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
  it("uses the saved output and selected language for summary translation", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(data)));
    vi.stubGlobal("fetch", fetch);
    selectProfile("alice");
    const requests = new TranscriptTranslationRequests("output/id", undefined, "summary");
    await requests.run("GET", "de");
    await requests.run("POST", "de");
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      "/v1/outputs/output%2Fid/translation?language=de",
      "/v1/outputs/output%2Fid/translation?language=de",
    ]);
    expect(fetch.mock.calls.map(([, options]) => options.method)).toEqual(["GET", "POST"]);
    expect(new Headers(fetch.mock.calls[1][1].headers).get("X-Omi-Profile")).toBe("alice");
  });
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
  it("shows Original and the target as compact tabs with a searchable language picker", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, translation: null }} />);
    expect(html).toContain('role="tablist"');
    for (const label of ["Original", "English"]) expect(html).toContain(`>${label}</button>`);
    expect(html).toContain('>German</option>');
    expect(html).not.toContain('>Japanese</option>');
    expect(html).toContain('type="search"');
    expect(html).toContain("<select");
    expect(html).toContain("<option");
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
    expect(html).toContain(">German</option>");
    expect(html).toContain(">Original</button>");
  });

  it("does not offer translation to the current matching target, including stale caches", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ ...data, already_in_target_language: true }} translated />);
    expect(html).toContain(original);
    expect(html).not.toContain(data.translation!.text);
    expect(html).not.toContain(">English</button>");
    expect(html).not.toContain("Translate to English");
  });

  it("offers no targets when translation languages are disabled in Settings", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{ language: "", available_languages: [], already_in_target_language: false, translation: null }} />);
    expect(html).not.toContain(">Japanese</option>");
    expect(html).not.toContain('aria-label="Find a translation language"');
    expect(html).toContain('href="/settings/general"');
    expect(html).toContain(original);
    expect(html).not.toContain("translation-trigger");
  });

  it("limits the picker to translation languages enabled in Settings", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={data} />);
    expect(html).toContain(">Danish</option>");
    expect(html).toContain(">English</option>");
    expect(html).not.toContain(">Japanese</option>");
  });

  it("offers translation into a dominant language for a mixed transcript", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} content="transcript" data={{
      ...data, translation: null, recommended_language: "da",
      language_analysis: { dominant_language: "da", dominant_language_share: 0.8,
        language_shares: { da: 0.8, en: 0.2 } },
    }} />);
    expect(html).toContain("Danish is dominant (about 80%)");
    expect(html).toContain("Translate to Danish");
    expect(html).toContain(original);
  });

  it("offers every enabled summary language as a tab without a transcript recommendation banner", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} content="summary" data={{
      ...data, translation: null, recommended_language: "da",
      language_analysis: { dominant_language: "da", dominant_language_share: 0.8,
        language_shares: { da: 0.8, en: 0.2 } },
    }} />);
    expect(html).toContain('aria-label="Summary language"');
    for (const label of ["Original", "English", "Danish", "German"])
      expect(html).toContain(`>${label}</button>`);
    expect(html).not.toContain("is dominant");
    expect(html).toContain(original);
  });

  it.each([0.9, 0.95, 1])("does not offer the dominant language when its share is %s", (share) => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{
      ...data, translation: null, recommended_language: "da",
      language_analysis: { dominant_language: "da", dominant_language_share: share,
        language_shares: { da: share, en: 1 - share } },
    }} />);
    expect(html).not.toContain("is dominant");
    expect(html).not.toContain("Translate to Danish");
  });

  it("does not offer a disabled dominant target or mistake uncertainty for mixed languages", () => {
    for (const changes of [
      { available_languages: ["en"] },
      { language_analysis: { dominant_language: "da", dominant_language_share: 0.8,
        language_shares: { da: 0.8 } } },
    ]) {
      const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={{
        ...data, translation: null, recommended_language: "da",
        language_analysis: { dominant_language: "da", dominant_language_share: 0.8,
          language_shares: { da: 0.8, en: 0.2 } }, ...changes,
      }} />);
      expect(html).not.toContain("is dominant");
      expect(html).not.toContain("Translate to Danish");
    }
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

  it("uses summary copy and rich content within the same translation tabs", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view} data={data} translated
      content="summary" renderText={(text) => <article><strong>{text}</strong></article>} />);
    expect(html).toContain('aria-label="Summary language"');
    expect(html).toContain("Generated translation · The original summary is preserved.");
    expect(html).toContain(`<article><strong>${data.translation!.text}</strong></article>`);
    expect(html).not.toContain("original transcript");
  });

  it("keeps summary controls in a hidden inert preview while the selected language loads", () => {
    const renderText = (text: string, translated: boolean) => translated
      ? <article>{text}</article> : <button>Original topic tools</button>;
    const originalHtml = renderToStaticMarkup(<TranscriptTranslationView {...view} data={data}
      content="summary" renderText={renderText} />);
    const pendingHtml = renderToStaticMarkup(<TranscriptTranslationView {...view}
      data={{ ...data, translation: null }} translated content="summary" renderText={renderText} />);
    expect(originalHtml).toContain("Original topic tools");
    expect(pendingHtml).toContain("Original topic tools");
    expect(pendingHtml).toContain('aria-hidden="true" inert=""');
    expect(pendingHtml).toContain("translation-preview-text");
    expect(pendingHtml).toContain("Translate to English");
  });

  it("offers regeneration for a saved translation that cannot preserve recap structure", () => {
    const html = renderToStaticMarkup(<TranscriptTranslationView {...view}
      data={{ ...data, translation: null, regeneration_required: true }} translated content="summary" />);
    expect(html).toContain("Update this translation to keep the recap’s topics and layout.");
    expect(html).toContain("Translate again");
    expect(html).not.toContain(data.translation!.text);
  });
});


it("remembers the last explicit translation target without requiring shared settings", () => {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (key: string) => values.get(key), setItem: (key: string, value: string) => values.set(key, value) });
  expect(preferredTranslationLanguage()).toBe("en");
  rememberTranslationLanguage("ja");
  expect(preferredTranslationLanguage()).toBe("ja");
  rememberTranslationLanguage("invalid");
  expect(preferredTranslationLanguage()).toBe("ja");
});
