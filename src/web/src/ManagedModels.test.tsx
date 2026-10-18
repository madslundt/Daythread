import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ManagedModelsPanel, ownModelUpdate } from "./ManagedModels";
import { managedKinds, type ManagedAccount, type ManagedMode } from "./managedService";

function account(mode: ManagedMode): ManagedAccount {
  return {
    id: "person", email: "person@example.com", name: "Person", hosted_until: 2_000_000_000,
    hosting_active: true, credits: { available_units: 1000, reserved_units: 0 },
    inference: Object.fromEntries(managedKinds.map((kind) => [kind, {
      mode, provider: "personal", model: `private-${kind}`, has_key: true, options: {},
    }])) as NonNullable<ManagedAccount["inference"]>,
    providers: [{ id: "personal", label: "Own provider", kinds: [...managedKinds] }],
    rates: { models: { stt: { model: "fixed-speech" }, llm: { model: "fixed-language" }, embedding: { model: "fixed-vectors" } } },
  };
}

describe("managed model settings", () => {
  it("offers three independent task choices and keeps Daythread models readonly", () => {
    const html = renderToStaticMarkup(<ManagedModelsPanel account={account("managed")} onUpdate={vi.fn()} />);
    for (const kind of managedKinds) expect(html).toContain(`name="model-${kind}-mode"`);
    expect(html.match(/>My own</g)).toHaveLength(3);
    expect(html.match(/>Off</g)).toHaveLength(3);
    for (const model of ["fixed-speech", "fixed-language", "fixed-vectors"]) expect(html).toContain(model);
    expect(html).not.toContain("<form");
    expect(html).not.toContain("private-stt");
    expect(html).toContain("Daythread chooses this model and its settings");
  });

  it("edits own model options and reuses saved keys without displaying secrets", () => {
    const html = renderToStaticMarkup(<ManagedModelsPanel account={account("own")} onUpdate={vi.fn()} />);
    expect(html.match(/<form/g)).toHaveLength(3);
    expect(html).toContain("private-stt");
    expect(html).toContain("Keep saved key");
    expect(html).toContain("Use saved key from Language models");
    expect(html).toContain("Use a new key");
    expect(html).not.toContain('type="password"');
    for (const option of ["Temperature", "Maximum output tokens", "Language (optional)", "Prompt (optional)", "Embedding dimensions", "Revision"]) expect(html).toContain(option);
  });

  it("explains independent Off effects while keeping hosting and the archive", () => {
    const html = renderToStaticMarkup(<ManagedModelsPanel account={account("off")} onUpdate={vi.fn()} />);
    expect(html).toContain("Off pauses new audio transcription");
    expect(html).toContain("Off pauses generated summaries, chat answers");
    expect(html).toContain("Keyword search stays available");
    expect(html).toContain("keeps your hosting, storage, and existing archive available");
    expect(html).not.toContain("<form");
  });

  it("serializes own provider options and never sends a managed credential or a retained secret", () => {
    const draft = { provider: "personal", model: " own-model ", key: "never-send-retained-secret", keySource: "keep", first: "0.2", second: "200" };
    expect(ownModelUpdate("llm", draft)).toEqual({ mode: "own", provider: "personal", model: "own-model", options: { temperature: 0.2, max_tokens: 200 } });
    expect(ownModelUpdate("embedding", { ...draft, keySource: "llm", first: "512", second: "v2" })).toEqual({ mode: "own", provider: "personal", model: "own-model", key_from: "llm", options: { dimensions: 512, revision: "v2" } });
    expect(ownModelUpdate("stt", { ...draft, keySource: "new", first: "", second: "" })).toEqual({ mode: "own", provider: "personal", model: "own-model", key: draft.key, options: {} });
    expect(() => ownModelUpdate("embedding", { ...draft, first: "" })).toThrow("Choose embedding dimensions");
    expect(() => ownModelUpdate("embedding", { ...draft, first: "0" })).toThrow("Choose embedding dimensions");
  });

  it("requires embedding dimensions before an own model can be saved", () => {
    const html = renderToStaticMarkup(<ManagedModelsPanel account={account("own")} onUpdate={vi.fn()} />);
    expect(html).toMatch(/Embedding dimensions<input[^>]*required=""/);
  });
});
