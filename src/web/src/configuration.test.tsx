import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelRoutes } from "./Configuration";
import {
  canRebaseConfiguration, configurationChanges, configurationDraft, configurationIsDirty,
  keepSecret, matchingConfigurationFields, routeChanges, routeRows, secretChange,
  type ConfigurationData,
} from "./configurationModel";

const configuration: ConfigurationData = {
  revision: "first", pending_restart: [], secrets: { inference_token: true, "stt_routes.da.token": true },
  values: {
    timezone: "UTC", stt_model: "speech", inference_timeout_seconds: 30,
    inference_token: null, stt_routes: { da: { model: "danish" }, en: { base_url: "http://local/v1" } },
  },
  fields: [
    { key: "timezone", label: "Timezone", group: "Archive", kind: "string", nullable: false,
      restart_required: false, description: "Calendar boundaries" },
    { key: "stt_model", label: "STT model", group: "Transcription", kind: "string", nullable: false,
      restart_required: false, description: "Speech model" },
    { key: "inference_timeout_seconds", label: "Inference timeout seconds", group: "Local inference", kind: "number",
      nullable: false, restart_required: false, description: "Request timeout" },
    { key: "inference_token", label: "Inference key", group: "Local inference", kind: "secret", nullable: false,
      restart_required: false, description: "Shared service credential" },
    { key: "stt_routes", label: "STT routes", group: "Transcription", kind: "routes", nullable: false,
      restart_required: false, description: "Language overrides" },
  ],
};

describe("configuration drafts", () => {
  it("preserves provider keys until explicitly replaced, disabled, or inherited", () => {
    const data: ConfigurationData = { ...configuration,
      values: { ...configuration.values, llm_api_key: null },
      secrets: { ...configuration.secrets, llm_api_key: true },
      fields: [...configuration.fields, { key: "llm_api_key", label: "LLM API key",
        group: "Language models & chat", kind: "secret", nullable: true,
        restart_required: false, description: "Provider credential" }],
    };
    const draft = configurationDraft(data);
    expect(configurationChanges(data, draft)).toEqual({});
    for (const [mode, key] of [["replace", "provider-key"], ["clear", ""], ["inherit", null]] as const) {
      draft.secrets.llm_api_key = { mode, value: mode === "replace" ? "provider-key" : "" };
      expect(configurationChanges(data, draft)).toEqual({ llm_api_key: key });
    }
  });
  it("does not send retained write-only keys when model fields change", () => {
    const rows = routeRows({ da: { model: "danish", base_url: null } });
    rows[0].model = "other";
    expect(routeChanges(rows, false)).toEqual({ da: { model: "other", base_url: null } });
    expect(secretChange(keepSecret())).toBeUndefined();
  });
  it("distinguishes replacing, disabling and inheriting route credentials", () => {
    expect(secretChange({ mode: "replace", value: "new-key" })).toBe("new-key");
    expect(secretChange({ mode: "clear", value: "" })).toBe("");
    expect(secretChange({ mode: "inherit", value: "" })).toBeNull();
    expect(() => secretChange({ mode: "replace", value: "" })).toThrow("replacement key");
  });
  it("rejects duplicate or invalid route names before a route could be overwritten", () => {
    const rows = routeRows({ da: { model: "one" }, en: { model: "two" } });
    rows[1].name = "DA";
    expect(() => routeChanges(rows, false)).toThrow("twice");
    rows[1].name = "not-a-task";
    expect(() => routeChanges(rows, true)).toThrow("select an LLM task");
  });
  it("renders usable route fields and explicit key actions without exposing a saved key", () => {
    const html = renderToStaticMarkup(<ModelRoutes
      field={{ key: "stt_routes", label: "STT routes", group: "Transcription", kind: "routes",
        nullable: false, restart_required: false, description: "Language routing" }}
      rows={routeRows({ da: { model: "danish" } })} secrets={{ "stt_routes.da.token": true }} onChange={() => {}}
      connectionDraft={configurationDraft(configuration)} revision={configuration.revision}
    />);
    expect(html).toContain("Language code or detector name");
    expect(html).toContain("Use provider key");
    expect(html).toContain("No authentication");
    expect(html).toContain("Configured");
    expect(html).not.toContain('type="password"');
  });
  it("keeps loaded routes and equivalent numeric input clean", () => {
    const draft = configurationDraft(configuration);
    draft.values.inference_timeout_seconds = "30";
    draft.routes.stt_routes.reverse();
    expect(configurationChanges(configuration, draft)).toEqual({});
    expect(configurationIsDirty(configuration, draft)).toBe(false);
  });
  it("saves only edits owned by this form without resending hidden preferences or retained keys", () => {
    const draft = configurationDraft(configuration);
    draft.values.timezone = "Europe/Copenhagen";
    draft.values.stt_model = "new-model";
    expect(configurationChanges(configuration, draft, ["timezone"])).toEqual({ stt_model: "new-model" });
    draft.values.stt_model = "speech";
    expect(configurationIsDirty(configuration, draft, ["timezone"])).toBe(false);
  });
  it("treats incomplete key and route edits as unsaved and validates before submission", () => {
    const draft = configurationDraft(configuration);
    draft.secrets.inference_token = { mode: "replace", value: "" };
    expect(configurationIsDirty(configuration, draft)).toBe(true);
    expect(() => configurationChanges(configuration, draft)).toThrow("replacement key");
    draft.secrets = {};
    draft.routes.stt_routes[0].name = "";
    expect(configurationIsDirty(configuration, draft)).toBe(true);
    expect(() => configurationChanges(configuration, draft)).toThrow("language code/name");
  });
  it("preserves explicit route key changes even when the model is unchanged", () => {
    const draft = configurationDraft(configuration);
    draft.routes.stt_routes[0].secret = { mode: "inherit", value: "" };
    expect(configurationIsDirty(configuration, draft)).toBe(true);
    expect(configurationChanges(configuration, draft)).toEqual({ stt_routes: {
      da: { model: "danish", base_url: null, token: null },
      en: { model: null, base_url: "http://local/v1" },
    } });
  });
  it("can advance the revision for a known preference save without losing server drafts", () => {
    const draft = configurationDraft(configuration);
    draft.values.stt_model = "draft-model";
    draft.secrets.inference_token = { mode: "replace", value: "replacement" };
    const latest = { ...configuration, revision: "second", values: { ...configuration.values, timezone: "Europe/Copenhagen" } };
    expect(canRebaseConfiguration(configuration, latest, ["timezone"])).toBe(true);
    expect(configurationChanges(latest, draft, ["timezone"])).toEqual({
      stt_model: "draft-model", inference_token: "replacement",
    });
  });
  it("does not rebase over another server edit, credential-status change, or unexplained revision", () => {
    const latest = { ...configuration, revision: "second", values: { ...configuration.values, timezone: "Europe/Copenhagen" } };
    expect(canRebaseConfiguration(configuration, { ...latest,
      values: { ...latest.values, stt_model: "external-model" },
    }, ["timezone"])).toBe(false);
    expect(canRebaseConfiguration(configuration, { ...latest,
      secrets: { ...latest.secrets, inference_token: false },
    }, ["timezone"])).toBe(false);
    expect(canRebaseConfiguration(configuration, { ...configuration, revision: "unexplained" }, ["timezone"])).toBe(false);
  });
  it("searches group names and descriptions while excluding fields edited elsewhere", () => {
    expect(matchingConfigurationFields(configuration.fields, "  TRANSCRIPTION ").map((field) => field.key))
      .toEqual(["stt_model", "stt_routes"]);
    expect(matchingConfigurationFields(configuration.fields, "credential").map((field) => field.key))
      .toEqual(["inference_token"]);
    expect(matchingConfigurationFields(configuration.fields, "timezone", ["timezone"])).toEqual([]);
    expect(matchingConfigurationFields(configuration.fields, "no match")).toEqual([]);
  });
});
