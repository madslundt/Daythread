import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelRoutes } from "./Configuration";
import { keepSecret, routeChanges, routeRows, secretChange } from "./configurationModel";

describe("configuration drafts", () => {
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
    />);
    expect(html).toContain("Language code or detector name");
    expect(html).toContain("Use shared key");
    expect(html).toContain("No authentication");
    expect(html).toContain("Configured");
    expect(html).not.toContain('type="password"');
  });
});
