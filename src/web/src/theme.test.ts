import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initializeTheme, setThemePreference, themeStorageKey } from "./theme";

class ColorScheme extends EventTarget {
  matches = false;
  change(dark: boolean) {
    this.matches = dark;
    this.dispatchEvent(new Event("change"));
  }
}

const media = new ColorScheme();
const stored = new Map<string, string>();
const root = { dataset: { theme: "" }, style: { colorScheme: "" } };
const meta = { setAttribute: vi.fn() };
let browser: EventTarget;
let cleanup: (() => void) | undefined;

beforeEach(() => {
  stored.clear();
  media.matches = false;
  browser = new EventTarget();
  Object.assign(browser, {
    matchMedia: () => media,
    localStorage: {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
    },
  });
  vi.stubGlobal("window", browser);
  vi.stubGlobal("document", {
    documentElement: root,
    querySelector: () => meta,
  });
});

afterEach(() => {
  cleanup?.();
  vi.unstubAllGlobals();
});

describe("appearance", () => {
  it("follows system changes by default, including native control colors", () => {
    cleanup = initializeTheme();
    expect(root.dataset.theme).toBe("light");
    media.change(true);
    expect(root.dataset.theme).toBe("dark");
    expect(root.style.colorScheme).toBe("dark");
    expect(meta.setAttribute).toHaveBeenLastCalledWith("content", "#171c21");
  });

  it("persists an override, ignores system changes, and can return to System", () => {
    cleanup = initializeTheme();
    setThemePreference("dark");
    expect(stored.get(themeStorageKey)).toBe("dark");
    media.change(false);
    expect(root.dataset.theme).toBe("dark");
    setThemePreference("system");
    expect(root.dataset.theme).toBe("light");
    media.change(true);
    expect(root.dataset.theme).toBe("dark");
  });

  it("restores the saved choice on startup", () => {
    stored.set(themeStorageKey, "light");
    media.matches = true;
    cleanup = initializeTheme();
    expect(root.dataset.theme).toBe("light");
  });

  it("synchronizes another tab and handles cleared or invalid preferences", () => {
    cleanup = initializeTheme();
    stored.set(themeStorageKey, "dark");
    browser.dispatchEvent(Object.assign(new Event("storage"), { key: themeStorageKey }));
    expect(root.dataset.theme).toBe("dark");
    stored.set(themeStorageKey, "unexpected");
    browser.dispatchEvent(Object.assign(new Event("storage"), { key: themeStorageKey }));
    expect(root.dataset.theme).toBe("light");
    stored.clear();
    media.matches = true;
    browser.dispatchEvent(Object.assign(new Event("storage"), { key: null }));
    expect(root.dataset.theme).toBe("dark");
  });

  it("still switches for this session when browser storage is blocked", () => {
    Object.defineProperty(browser, "localStorage", {
      get: () => { throw new Error("Storage blocked"); },
    });
    cleanup = initializeTheme();
    expect(root.dataset.theme).toBe("light");
    expect(() => setThemePreference("dark")).not.toThrow();
    expect(root.dataset.theme).toBe("dark");
  });
});
