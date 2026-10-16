import { useSyncExternalStore } from "react";

export type ThemePreference = "system" | "light" | "dark";
export const themeStorageKey = "omi:appearance:v1";
let preference: ThemePreference = "system";
const listeners = new Set<() => void>();

export function parseThemePreference(value: string | null): ThemePreference {
  return value === "light" || value === "dark" ? value : "system";
}

function readPreference(): ThemePreference {
  try {
    return parseThemePreference(window.localStorage.getItem(themeStorageKey));
  } catch {
    return "system";
  }
}

function applyTheme(): void {
  const theme = preference === "system"
    ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : preference;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute(
    "content", theme === "dark" ? "#171c21" : "#f7f5f0",
  );
}

function notify(): void {
  applyTheme();
  listeners.forEach((listener) => listener());
}

export function initializeTheme(): () => void {
  preference = readPreference();
  applyTheme();
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const syncSystem = () => { if (preference === "system") applyTheme(); };
  const syncStorage = (event: StorageEvent) => {
    if (event.key === themeStorageKey || event.key === null) {
      preference = readPreference();
      notify();
    }
  };
  media.addEventListener("change", syncSystem);
  window.addEventListener("storage", syncStorage);
  return () => {
    media.removeEventListener("change", syncSystem);
    window.removeEventListener("storage", syncStorage);
  };
}

export function setThemePreference(next: ThemePreference): void {
  preference = next;
  try { window.localStorage.setItem(themeStorageKey, next); } catch { /* Session-only when storage is blocked. */ }
  notify();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(subscribe, () => preference, () => "system");
}
