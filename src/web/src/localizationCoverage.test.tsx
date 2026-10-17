import { afterEach, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Loading } from "./UI";
import { ModelRoutes } from "./Configuration";
import { matchingConfigurationFields } from "./configurationModel";
import { statusLabel } from "./statusLabels";
import { source, t, setLanguagePreference } from "./i18n";

afterEach(() => setLanguagePreference("en"));

it("localizes default loading copy", () => {
  setLanguagePreference("da");
  expect(renderToStaticMarkup(<Loading />)).toContain("Indlæser");
});

it("localizes setting descriptions supplied by the server", () => {
  setLanguagePreference("da");
  const html = renderToStaticMarkup(<ModelRoutes field={{ key: "stt_routes", label: "STT routes",
    description: "Override model, endpoint or token per language code (da, en), detector name (danish), or auto. Blank model/endpoint inherits the defaults. Automatic routing needs a multilingual default model that returns language metadata.",
    group: "Transcription", kind: "routes", nullable: false, restart_required: false }} rows={[]}
    secrets={{}} onChange={() => {}} connectionDraft={{ values: {}, routes: {}, secrets: {} }} revision="test" />);
  expect(html).not.toContain("Override model, endpoint or token");
  expect(html).toContain("flersproget");
  expect(matchingConfigurationFields([{ key: "stt_routes", label: "STT routes",
    description: "Transcription", group: "Transcription", kind: "routes",
    nullable: false, restart_required: false }], "transskription")).toHaveLength(1);
});

it("localizes protocol display labels while keeping values and user text intact", () => {
  setLanguagePreference("da");
  const state = "pending";
  expect(statusLabel(state)).toBe("Afventer");
  expect(state).toBe("pending");
  expect(source("Today")).toBe("Today");
  expect(t("Remove {value0}", { value0: "Today" })).toContain("Today");
  expect(t("Unknown provider diagnostic")).toBe("Unknown provider diagnostic");
  setLanguagePreference("en");
  expect(statusLabel(state)).toBe("Pending");
});

it("localizes stored message templates without changing interpolated data", () => {
  setLanguagePreference("da");
  expect(t("Remove Today")).toBe("Fjern Today");
  const translated = t("Home-core HTTP 400: Today");
  expect(translated).toContain("400");
  expect(translated).toContain("Today");
  expect(t("Remove {value0}", { value0: "{value1}" })).toContain("{value1}");
});
