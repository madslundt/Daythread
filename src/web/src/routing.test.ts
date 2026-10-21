import { afterEach, describe, expect, it, vi } from "vitest";
import { dayFromPath, pageFromPath, pagePath, profileRoute, legacyRoutePath, navigatePath, subscribeNavigation, recapTopicPath, requestedRecapTopic } from "./routing";
import { localDay } from "./format";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("recap topic navigation", () => {
  it("encodes canonical titles without confusing query characters or translated labels", () => {
    const title = "Garden & plans / ☘️ + #1";
    const path = recapTopicPath("2026-10-20", title)!;
    const url = new URL(path, "https://daythread.home.arpa");
    expect(url.pathname).toBe("/diary/2026-10-20");
    expect(requestedRecapTopic(url.pathname, url.search, "2026-10-20")).toBe(title);
    expect(requestedRecapTopic(url.pathname, url.search, "2026-10-19")).toBeUndefined();
  });
  it("keeps identical titles on different days distinct", () => {
    expect(recapTopicPath("2026-10-19", "Garden")).not.toBe(recapTopicPath("2026-10-20", "Garden"));
  });
  it("rejects missing, malformed or ambiguous topic destinations", () => {
    expect(recapTopicPath("2026-02-30", "Garden")).toBeUndefined();
    expect(recapTopicPath(undefined, "Garden")).toBeUndefined();
    expect(recapTopicPath("2026-10-20", " ")).toBeUndefined();
    expect(requestedRecapTopic("/settings", "?topic=Garden", "2026-10-20")).toBeUndefined();
    expect(requestedRecapTopic("/diary/2026-10-20", "?topic=Garden&topic=Other", "2026-10-20")).toBeUndefined();
  });
});

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
  it("reopens a topic at the same URL without duplicating browser history", () => {
    const browser = new EventTarget();
    const location = new URL("https://daythread.home.arpa/diary/2026-10-20?topic=Garden");
    const pushState = vi.fn();
    Object.assign(browser, { location, history: { pushState } });
    vi.stubGlobal("window", browser);
    const listener = vi.fn();
    const unsubscribe = subscribeNavigation(listener);
    navigatePath("/diary/2026-10-20?topic=Garden", { reselect: true });
    expect(listener).toHaveBeenCalledOnce();
    expect(pushState).not.toHaveBeenCalled();
    unsubscribe();
  });
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


describe("profile selection routes", () => {
  it.each(["/", "/today", "/today/2026-10-16", "/diary/2026-10-16", "/rewind", "/chat", "/inbox", "/labels/person-id"])("requires a profile before opening %s", (path) => {
    expect(profileRoute(path, null)).toBe("/settings/profiles");
    expect(profileRoute(path, "work")).toBe(path);
  });

  it.each(["/settings", "/settings/profiles", "/settings/general", "/settings/connections", "/settings/storage", "/settings/gestures", "/settings/server"])("allows %s without a profile", (path) => {
    expect(profileRoute(path, null)).toBe(path);
  });
});
