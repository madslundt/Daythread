import { afterEach, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Loading } from "./UI";
import { ModelRoutes } from "./Configuration";
import { matchingConfigurationFields } from "./configurationModel";
import { statusLabel } from "./statusLabels";
import { source, t, setLanguagePreference, interfaceLanguages } from "./i18n";
import { pages } from "./Navigation";
import { matchesSettings } from "./SettingsNavigation";

const catalogs = import.meta.glob<Record<string, string>>("./locales/*.json", { eager: true, import: "default" });

afterEach(() => setLanguagePreference("en"));

it.each(interfaceLanguages)("loads the complete %s catalog across every page", (language) => {
  setLanguagePreference(language);
  const catalog = catalogs[`./locales/${language}.json`];
  expect(catalog).toBeDefined();
  expect(Object.keys(catalog).sort()).toEqual(Object.keys(catalogs["./locales/en.json"]).sort());
  for (const [key, translation] of Object.entries(catalog)) {
    expect(t(key), `${language}: ${key}`).toBe(translation);
  }
  for (const page of pages) {
    expect(catalog[page.label]).toBeDefined();
    expect(catalog[page.title]).toBeDefined();
  }
  if (language !== "en") {
    for (const key of ["Today", "Settings", "Loading…", "Save preferences", "Interface language", "System language"]) {
      expect(t(key), `${language}: ${key}`).not.toBe(key);
    }
  }
});

it.each(interfaceLanguages.filter((language) => language !== "en"))(
  "preserves user data in %s interpolated and stored messages", (language) => {
    setLanguagePreference(language);
    const name = "Today {value1} 東京";
    expect(t("Remove {value0}", { value0: name })).toContain(name);
    expect(t(`Remove ${name}`)).toContain(name);
    expect(t("Unknown provider diagnostic")).toBe("Unknown provider diagnostic");
    expect(statusLabel("pending")).not.toBe("status.pending");
    expect(source("Today")).toBe("Today");
  },
);

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

it("finds settings by translated words regardless of capitalization", () => {
  setLanguagePreference("de");
  expect(matchesSettings("appearance", "Darstellung")).toBe(true);
  expect(matchesSettings("appearance", "DARSTELLUNG")).toBe(true);
  expect(matchesSettings("storage", "Speicher")).toBe(true);
  expect(matchesSettings("storage", "SPEICHER")).toBe(true);
  expect(matchesSettings("storage", "appearance")).toBe(false);
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
