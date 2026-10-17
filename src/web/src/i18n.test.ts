import { afterEach, describe, expect, it, vi } from "vitest";
import { displayLocale, interfaceLanguage, interfaceLanguages, languagePreference, setLanguagePreference, t } from "./i18n";
import { dayLabel } from "./format";
import { monthLabel } from "./calendarDates";

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

  it.each([
    ["de-AT", "de", "Heute"],
    ["es-MX", "es", "Hoy"],
    ["fr-CA", "fr", "Aujourd’hui"],
    ["nb-NO", "nb", "I dag"],
    ["no-NO", "nb", "I dag"],
    ["sv-SE", "sv", "Idag"],
  ])("resolves %s to its supported interface catalog", (device, expected, today) => {
    vi.stubGlobal("navigator", { languages: [device, "en-US"] });
    setLanguagePreference("system");
    expect(interfaceLanguage()).toBe(expected);
    expect(t("Today")).toBe(today);
  });

  it("follows browser language priority and falls back to English", () => {
    vi.stubGlobal("navigator", { languages: ["ja-JP", "fr-FR", "de-DE"] });
    setLanguagePreference("system");
    expect(interfaceLanguage()).toBe("fr");
    vi.stubGlobal("navigator", { languages: ["ja-JP"] });
    expect(interfaceLanguage()).toBe("en");
  });

  it.each(interfaceLanguages)("persists %s and uses it for date formatting", (language) => {
    const saved = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => saved.get(key) ?? null,
      setItem: (key: string, value: string) => saved.set(key, value),
      removeItem: (key: string) => saved.delete(key),
    });
    vi.stubGlobal("navigator", { languages: ["en-US"] });
    setLanguagePreference(language);
    expect(saved.get("daythread.interfaceLanguage")).toBe(language);
    expect(languagePreference()).toBe(language);
    const date = new Date("2026-10-17T12:00:00Z");
    expect(dayLabel("2026-10-17")).toBe(new Intl.DateTimeFormat(displayLocale(), {
      year: "numeric", month: "long", day: "numeric", timeZone: "UTC",
    }).format(date));
    expect(monthLabel("2026-10")).toBe(new Intl.DateTimeFormat(displayLocale(), {
      year: "numeric", month: "long", timeZone: "UTC",
    }).format(date));
    setLanguagePreference("system");
    expect(saved.has("daythread.interfaceLanguage")).toBe(false);
    expect(interfaceLanguage()).toBe("en");
  });

  it("keeps the language usable when browser storage is unavailable", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => { throw new Error("Storage disabled"); },
      setItem: () => { throw new Error("Storage disabled"); },
      removeItem: () => { throw new Error("Storage disabled"); },
    });
    setLanguagePreference("es");
    expect(t("Today")).toBe("Hoy");
  });
});
