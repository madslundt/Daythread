import { afterEach, describe, expect, it, vi } from "vitest";
import { interfaceLanguage, setLanguagePreference, t } from "./i18n";

describe("interface localization", () => {
  afterEach(() => {
    setLanguagePreference("system");
    vi.unstubAllGlobals();
  });

  it("uses a supported device language and lets this device override it", () => {
    vi.stubGlobal("navigator", { languages: ["da-DK", "en-US"], language: "da-DK" });
    setLanguagePreference("system");
    expect(interfaceLanguage()).toBe("da");
    expect(t("Today")).toBe("I dag");

    setLanguagePreference("en");
    expect(t("Today")).toBe("Today");
    setLanguagePreference("da");
    expect(t("A new untranslated string")).toBe("A new untranslated string");
    expect(t("{count} new recordings", { count: 2 })).toBe("2 new recordings");
  });
});
