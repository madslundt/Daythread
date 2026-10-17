import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelPicker } from "./ModelPicker";
import { keepSecret, routeRows, type ConfigurationDraft } from "./configurationModel";
import { modelConnectionRequest } from "./modelConnection";

function draft(): ConfigurationDraft {
  return {
    values: { stt_base_url: "http://speech.local/v1", stt_model: "speech-default",
      llm_base_url: "http://chat.local/v1", llm_model: "chat-default", llm_enable_thinking: false,
      timezone: "unfinished preference", inference_token: null },
    routes: { stt_routes: routeRows({ da: { base_url: "http://danish.local/v1", model: "danish" } }),
      llm_routes: [] }, secrets: {},
  };
}

describe("model connection previews", () => {
  it("only sends the chosen default connection without unrelated edits or retained keys", () => {
    const value = draft();
    value.routes.stt_routes[0].name = "";
    value.secrets.omi_api_key = { mode: "replace", value: "" };
    expect(modelConnectionRequest(value, "llm")).toEqual({ provider: "llm", changes: {
      llm_base_url: "http://chat.local/v1", llm_model: "chat-default", llm_enable_thinking: false,
    } });
    expect(value.secrets.inference_token).toBeUndefined();
  });
  it("looks up a catalog even while a custom model is blank", () => {
    const value = draft();
    value.values.stt_model = "";
    expect(modelConnectionRequest(value, "stt", undefined, false)).toEqual({ provider: "stt",
      changes: { stt_base_url: "http://speech.local/v1" } });
  });
  it("uses draft endpoint and shared key without requiring a settings save", () => {
    const value = draft();
    value.values.stt_base_url = " http://new-speech.local/v1 ";
    value.secrets.inference_token = { mode: "replace", value: "new-test-key" };
    expect(modelConnectionRequest(value, "stt").changes).toEqual({
      stt_base_url: "http://new-speech.local/v1", stt_model: "speech-default", inference_token: "new-test-key",
    });
    value.secrets.inference_token = { mode: "replace", value: "" };
    expect(() => modelConnectionRequest(value, "stt")).toThrow("replacement key");
  });
  it("targets one route and preserves keep, inherit and no-authentication semantics", () => {
    const value = draft();
    const row = value.routes.stt_routes[0];
    row.name = " DA ";
    expect(modelConnectionRequest(value, "stt", row)).toEqual({ provider: "stt", route: "da", changes: {
      stt_base_url: "http://speech.local/v1", stt_model: "speech-default",
      stt_routes: { da: { base_url: "http://danish.local/v1", model: "danish" } },
    } });
    for (const [mode, token] of [["inherit", null], ["clear", ""]] as const) {
      row.secret = { mode, value: "" };
      expect(modelConnectionRequest(value, "stt", row).changes.stt_routes).toEqual({
        da: { base_url: "http://danish.local/v1", model: "danish", token },
      });
    }
    expect(keepSecret()).toEqual({ mode: "keep", value: "" });
  });
  it("previews provider keys for defaults and routes without including another provider's key", () => {
    const value = draft();
    value.secrets.llm_api_key = { mode: "replace", value: "" };
    for (const [mode, key] of [["replace", "speech-key"], ["clear", ""], ["inherit", null]] as const) {
      value.secrets.stt_api_key = { mode, value: mode === "replace" ? "speech-key" : "" };
      for (const row of [undefined, value.routes.stt_routes[0]]) {
        const changes = modelConnectionRequest(value, "stt", row).changes;
        expect(changes.stt_api_key).toBe(key);
        expect(changes.llm_api_key).toBeUndefined();
      }
    }
  });
  it("does not tie route catalog requests to the selected model", () => {
    const value = draft();
    const row = value.routes.stt_routes[0];
    const first = modelConnectionRequest(value, "stt", row, false);
    row.model = "another-model";
    expect(modelConnectionRequest(value, "stt", row, false)).toEqual(first);
  });
  it("renders a model dropdown with a Custom choice, lookup and test controls", () => {
    const html = renderToStaticMarkup(<ModelPicker id="stt-model" label="STT model" value="speech-default"
      onChange={() => {}} provider="stt" draft={draft()} revision="first" />);
    expect(html).toContain('<select id="stt-model"');
    expect(html).toContain('value="custom"');
    expect(html).toContain("Test connection");
    expect(html).toContain("Refresh models");
    expect(html).toContain("short synthetic audio clip");
    expect(html).not.toContain('type="password"');
  });
});
