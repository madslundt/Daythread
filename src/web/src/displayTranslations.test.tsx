import { afterEach, describe, expect, it, vi } from "vitest";
import { DisplayTranslationRequests, displayTranslationKey, type DisplayTranslationInput } from "./displayTranslations";
import { originalTopicTranslation } from "./recapItems";
import { selectProfile } from "./profiles";

const action: DisplayTranslationInput = { kind: "inbox", id: "action", version: 2, original_title: "Send the launch brief" };
const choice: DisplayTranslationInput = { kind: "decision", id: "decision", version: 3, original_title: "Start with a smaller pilot" };
const result = (item = action, title = "Send lanceringsoplægget") => ({ ...item, title, content_key: "canonical-content-key" });
afterEach(() => { selectProfile("default"); vi.unstubAllGlobals(); });

describe("canonical overview display translation requests", () => {
  it("sends only canonical references and selected language in the same profile", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ language: "da", items: [result()] })));
    vi.stubGlobal("fetch", fetch); selectProfile("alice");
    const input = structuredClone(action);
    await expect(new DisplayTranslationRequests().run("da", [input])).resolves.toEqual([result()]);
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe("/v1/display-translations");
    expect(JSON.parse(options.body)).toEqual({ language: "da", items: [{ kind: "inbox", id: "action", version: 2 }] });
    expect(new Headers(options.headers).get("X-Omi-Profile")).toBe("alice");
    expect(input).toEqual(action);
  });

  it.each(["language", "record", "version", "title", "duplicate", "empty"])("rejects %s mismatches instead of applying a label to another record", async (failure) => {
    const item = result();
    const response = { language: "da", items: [item] };
    if (failure === "language") response.language = "de";
    if (failure === "record") item.id = "another-action";
    if (failure === "version") item.version += 1;
    if (failure === "title") item.original_title = "A changed canonical title";
    if (failure === "duplicate") response.items.push(item);
    if (failure === "empty") item.title = " ";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(response))));
    await expect(new DisplayTranslationRequests().run("da", [action])).rejects.toThrow("The overview translation could not be loaded");
  });

  it("chunks large overviews at fifty references without combining or losing row identities", async () => {
    const inputs = Array.from({ length: 103 }, (_, index) => ({ ...action, id: `action-${index}`, original_title: `Task ${index}` }));
    const fetch = vi.fn().mockImplementation(async (_url, options) => {
      const { items } = JSON.parse(options.body);
      return new Response(JSON.stringify({ language: "da", items: items.map((ref: DisplayTranslationInput) =>
        result(inputs.find((input) => input.id === ref.id)!, `Opgave ${ref.id.split("-")[1]}`)) }));
    });
    vi.stubGlobal("fetch", fetch);
    const translated = await new DisplayTranslationRequests().run("da", inputs);
    expect(fetch.mock.calls.map(([, options]) => JSON.parse(options.body).items.length)).toEqual([50, 50, 3]);
    expect(translated?.map((item) => item.id)).toEqual(inputs.map((item) => item.id));
  });

  it.each([null, { language: "da", items: [null] }])("keeps malformed null payloads actionable", async (response) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(response))));
    await expect(new DisplayTranslationRequests().run("da", [action])).rejects.toThrow("The overview translation could not be loaded");
  });

  it("discards an older target after cancellation and a delayed response after profile changes", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    selectProfile("alice");
    const client = new DisplayTranslationRequests();
    const pending = client.run("da", [action]);
    client.cancel();
    finish(new Response(JSON.stringify({ language: "da", items: [result()] })));
    await expect(pending).resolves.toBeUndefined();
    const next = client.run("da", [action]);
    selectProfile("bob");
    finish(new Response(JSON.stringify({ language: "da", items: [result()] })));
    await expect(next).resolves.toBeUndefined();
    await expect(client.run("da", [action])).resolves.toBeUndefined();
  });

  it("keeps source-only topic labels tied to exact current Unicode offsets, versions and chapter", () => {
    const input = originalTopicTranslation({ id: "original:chapter", title: "Weekend plans", parts: [], originalPassages: [{
      source_id: "source", source_version: "source-v1", transcript_version: "transcript-v1", text: "☕ Garden plans",
      section: { title: "Weekend plans", start_offset: 4, end_offset: 18, chapter_id: "chapter" },
    }] });
    expect(input).toEqual({ kind: "source_topic", id: "source", source_version: "source-v1", transcript_version: "transcript-v1",
      start_offset: 4, end_offset: 18, chapter_id: "chapter", original_title: "Weekend plans" });
    expect(displayTranslationKey(input!)).not.toBe(displayTranslationKey({ ...input!, original_title: "A renamed topic" }));
    expect(displayTranslationKey(action)).not.toBe(displayTranslationKey(choice));
  });
});
