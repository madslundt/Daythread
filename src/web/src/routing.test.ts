import { afterEach, describe, expect, it, vi } from "vitest";
import { dayFromPath, pageFromPath, pagePath, legacyRoutePath, navigatePath, subscribeNavigation } from "./routing";
import { localDay } from "./format";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("date deep links", () => {
  it.each(["today", "diary"] as const)("restores a selected date on %s after refresh", (page) => {
    const path = pagePath(page, "2026-10-16");
    expect(path).toBe(`/${page}/2026-10-16`);
    expect(pageFromPath(path)).toBe(page);
    expect(dayFromPath(path)).toBe("2026-10-16");
  });

  it.each(["today", "diary"] as const)("keeps %s relative to the current day in the home timezone", (page) => {
    vi.useFakeTimers();
    const path = pagePath(page);
    const restoredDay = () => dayFromPath(path) ?? localDay("Europe/Copenhagen");
    vi.setSystemTime(new Date("2026-10-16T21:59:00Z"));
    expect(restoredDay()).toBe("2026-10-16");
    vi.setSystemTime(new Date("2026-10-16T22:01:00Z"));
    expect(restoredDay()).toBe("2026-10-17");
    expect(path).toBe(`/${page}`);
  });

  it("keeps an explicit date fixed even when it was today when opened", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-16T12:00:00Z"));
    const path = pagePath("today", localDay("Europe/Copenhagen"));
    vi.setSystemTime(new Date("2026-10-17T12:00:00Z"));
    expect(dayFromPath(path)).toBe("2026-10-16");
  });

  it.each(["", "/today", "/diary", "/today/today", "/today/2026-02-30", "/diary/2026-02-29", "/today/2026-13-01", "/today/0000-01-01", "/today/2026-1-01", "/today/%ZZ", "/today/2026-10-16/extra", "/unknown/2026-10-16"])("falls back to today for invalid or missing dates: %s", (path) => {
    expect(dayFromPath(path)).toBeUndefined();
  });

  it("accepts leap days and the earliest calendar year", () => {
    expect(dayFromPath("/diary/2024-02-29")).toBe("2024-02-29");
    expect(dayFromPath("/today/0001-01-01")).toBe("0001-01-01");
  });

  it("preserves existing settings and people routes", () => {
    expect(pageFromPath("/settings/connections")).toBe("settings");
    expect(pageFromPath("/labels/person-id")).toBe("labels");
    expect(dayFromPath("/labels/2026-10-16")).toBeUndefined();
    expect(pagePath("settings", "2026-10-16")).toBe("/settings");
    expect(pageFromPath("/unknown")).toBe("today");
  });
});

describe("path navigation", () => {
  it.each(["today/2026-10-16", "diary/2026-10-16", "settings/connections", "labels/person-id"])("converts an old bookmark for %s", (route) => {
    expect(legacyRoutePath("/", `#${route}`)).toBe(`/${route}`);
  });

  it("leaves document anchors and existing paths alone", () => {
    expect(legacyRoutePath("/", "#main-content")).toBeUndefined();
    expect(legacyRoutePath("/", "#//other.example")).toBeUndefined();
    expect(legacyRoutePath("/diary/2026-10-16", "#today")).toBeUndefined();
    expect(pageFromPath("/")).toBe("today");
  });

  it("updates history and notifies subscribers for both navigation and Back/Forward", () => {
    const browser = new EventTarget();
    const location = new URL("http://omi.local/today");
    const pushState = vi.fn((_state: unknown, _unused: string, path: string) => {
      location.href = new URL(path, location).href;
    });
    Object.assign(browser, { location, history: { pushState } });
    vi.stubGlobal("window", browser);
    const listener = vi.fn();
    const unsubscribe = subscribeNavigation(listener);
    navigatePath("/today/2026-10-16");
    expect(location.pathname).toBe("/today/2026-10-16");
    expect(location.hash).toBe("");
    expect(pushState).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledTimes(1);
    navigatePath("/today/2026-10-16");
    expect(pushState).toHaveBeenCalledTimes(1);
    browser.dispatchEvent(new Event("popstate"));
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    navigatePath("/diary/2026-10-16");
    browser.dispatchEvent(new Event("popstate"));
    expect(listener).toHaveBeenCalledTimes(2);
  });
});
