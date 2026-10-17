import { useSyncExternalStore } from "react";
import danish from "./locales/da.json";

// English copy in the components is the source catalog and the fallback.
// Add a locale here and in locales/ to expose another interface language.
export const interfaceLanguages = ["en", "da"] as const;
export type InterfaceLanguage = typeof interfaceLanguages[number];
export type LanguagePreference = InterfaceLanguage | "system";

const storageKey = "daythread.interfaceLanguage";
const catalogs: Record<string, Record<string, string>> = { da: danish };
const listeners = new Set<() => void>();
let unsavedPreference: LanguagePreference | undefined;
let watchingDeviceLanguage = false;

function deviceLanguage(): InterfaceLanguage {
  const languages = typeof navigator === "undefined" ? [] : navigator.languages?.length
    ? navigator.languages : [navigator.language];
  for (const language of languages) {
    const base = language.toLowerCase().split("-")[0];
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
  if (typeof document !== "undefined") document.documentElement.lang = interfaceLanguage();
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

export function t(english: string, values?: Record<string, string | number>): string {
  const translated = catalogs[interfaceLanguage()]?.[english] || english;
  return values ? translated.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g,
    (placeholder, key: string) => key in values ? String(values[key]) : placeholder) : translated;
}
