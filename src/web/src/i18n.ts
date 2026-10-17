import { useSyncExternalStore } from "react";
import danish from "./locales/da.json";
import german from "./locales/de.json";
import spanish from "./locales/es.json";
import french from "./locales/fr.json";
import norwegian from "./locales/nb.json";
import swedish from "./locales/sv.json";
import interfaceLabels from "../../../config/interface-labels.json";
import { isManagedOrigin } from "./managedService";

// English copy in the components is the source catalog and the fallback.
// Add a locale here and in locales/ to expose another interface language.
export const interfaceLanguages = ["en", "da", "de", "es", "fr", "nb", "sv"] as const;
export type InterfaceLanguage = typeof interfaceLanguages[number];
export type LanguagePreference = InterfaceLanguage | "system";

const storageKey = "daythread.interfaceLanguage";
const catalogs: Record<string, Record<string, string>> = {
  en: Object.fromEntries(Object.entries(interfaceLabels).map(([code, label]) => [`status.${code}`, label])),
  da: danish,
  de: german,
  es: spanish,
  fr: french,
  nb: norwegian,
  sv: swedish,
};
const listeners = new Set<() => void>();
let unsavedPreference: LanguagePreference | undefined;
let watchingDeviceLanguage = false;

function deviceLanguage(): InterfaceLanguage {
  const languages = typeof navigator === "undefined" ? [] : navigator.languages?.length
    ? navigator.languages : [navigator.language];
  for (const language of languages) {
    if (typeof language !== "string") continue;
    const code = language.toLowerCase().split(/[-_]/)[0];
    const base = code === "no" ? "nb" : code;
    if (interfaceLanguages.some((item) => item === base)) return base as InterfaceLanguage;
  }
  return "en";
}

export function languagePreference(): LanguagePreference {
  if (unsavedPreference) return unsavedPreference;
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved && (interfaceLanguages as readonly string[]).includes(saved)) return saved as InterfaceLanguage;
  } catch { /* Private browsing can disable storage. */ }
  return "system";
}

export function interfaceLanguage(): InterfaceLanguage {
  const preference = languagePreference();
  return preference === "system" ? deviceLanguage() : preference;
}

export function displayLocale(): string {
  const language = interfaceLanguage();
  return language === "en" ? "en-US" : language;
}

export function initializeLanguage(): void {
  if (typeof document !== "undefined") {
    document.documentElement.lang = interfaceLanguage();
    document.querySelector('meta[name="description"]')?.setAttribute("content",
      isManagedOrigin() ? t("Your private audio memory, hosted by Daythread.") : t("Your private audio memory, on your own home server."));
  }
  if (typeof window !== "undefined" && !watchingDeviceLanguage) {
    window.addEventListener("languagechange", () => {
      initializeLanguage();
      listeners.forEach((listener) => listener());
    });
    watchingDeviceLanguage = true;
  }
}

export function setLanguagePreference(value: LanguagePreference): void {
  if (value !== "system" && !(interfaceLanguages as readonly string[]).includes(value)) return;
  unsavedPreference = value;
  try {
    if (value === "system") localStorage.removeItem(storageKey);
    else localStorage.setItem(storageKey, value);
  } catch { /* The choice remains usable for this render. */ }
  initializeLanguage();
  listeners.forEach((listener) => listener());
}

export function useInterfaceLanguage(): InterfaceLanguage {
  return useSyncExternalStore(
    (listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    interfaceLanguage,
    () => "en",
  );
}

export function useLanguagePreference(): LanguagePreference {
  return useSyncExternalStore(
    (listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    languagePreference,
    () => "system",
  );
}

/** Keep catalog keys in module-level data untranslated until they are displayed. */
export function source(english: string): string { return english; }

const messagePatterns = new Map<string, { pattern: RegExp; names: string[]; translated: string }[]>();
function translatedMessage(message: string, language: string): string | undefined {
  const catalog = catalogs[language];
  if (!catalog) return;
  if (catalog[message]) return catalog[message];
  if (language === "en" || message.length > 8192) return;
  if (!messagePatterns.has(language)) {
    const patterns = Object.entries(catalog).filter(([key]) => /\{\w+\}/.test(key)).map(([key, translated]) => {
      const names: string[] = [];
      const parts = key.split(/(\{\w+\})/g).map((part) => {
        if (/^\{\w+\}$/.test(part)) { names.push(part.slice(1, -1)); return "([\\s\\S]*?)"; }
        return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      });
      return { pattern: new RegExp(`^${parts.join("")}$`), names, translated, specificity: key.replace(/\{\w+\}/g, "").length };
    }).sort((a, b) => b.specificity - a.specificity);
    messagePatterns.set(language, patterns);
  }
  for (const { pattern, names, translated } of messagePatterns.get(language)!) {
    const match = pattern.exec(message);
    if (match) return translated.replace(/\{(\w+)\}/g, (placeholder, name: string) => {
      const index = names.indexOf(name);
      return index < 0 ? placeholder : match[index + 1];
    });
  }
}

export function t(english: string, values?: Readonly<Record<string, unknown>>): string {
  const language = interfaceLanguage();
  const translated = (values ? catalogs[language]?.[english] : translatedMessage(english, language)) || english;
  return values ? translated.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g,
    (placeholder, key: string) => key in values ? String(values[key]) : placeholder) : translated;
}
