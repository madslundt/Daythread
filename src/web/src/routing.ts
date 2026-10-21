import { pages, type Page } from "./Navigation";

export function pageFromPath(path: string): Page {
  const key = path.replace(/^\//, "").split("/")[0];
  return pages.find((page) => page.id === key)?.id ?? "today";
}

export function profileRoute(path: string, selected: string | null): string {
  return selected || pageFromPath(path) === "settings" ? path : "/settings/profiles";
}

export function isDayPage(page: Page): boolean {
  return page === "today" || page === "diary";
}

export function dayFromPath(path: string): string | undefined {
  if (!isDayPage(pageFromPath(path))) return;
  const [page, day, extra] = path.replace(/^\//, "").split("/");
  if ((page !== "today" && page !== "diary") || extra !== undefined) return;
  if (!day || !/^(?!0000)\d{4}-\d{2}-\d{2}$/.test(day)) return;
  const date = new Date(`${day}T12:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === day
    ? day : undefined;
}

export function pagePath(page: Page, day?: string): string {
  return `/${page}${isDayPage(page) && day ? `/${day}` : ""}`;
}

export function recapTopicPath(day: string | undefined, title: string): string | undefined {
  if (!day || dayFromPath(`/diary/${day}`) !== day || !title.trim()) return;
  return `/diary/${day}?${new URLSearchParams({ topic: title })}`;
}

export function requestedRecapTopic(path: string, search: string, day: string): string | undefined {
  if (dayFromPath(path) !== day) return;
  const topics = new URLSearchParams(search).getAll("topic");
  return topics.length === 1 && topics[0].trim() ? topics[0] : undefined;
}

function isAppPath(path: string): boolean {
  return path === "/" || pages.some((page) => page.id === path.split("/")[1]);
}

export function legacyRoutePath(pathname: string, hash: string): string | undefined {
  if (pathname !== "/" || !hash.startsWith("#")) return;
  const path = `/${hash.slice(1)}`;
  return path !== "/" && isAppPath(path) ? path : undefined;
}

export function navigatePath(path: string, { replace = false, reselect = false } = {}): void {
  if (`${window.location.pathname}${window.location.search}${window.location.hash}` === path) {
    if (reselect) window.dispatchEvent(new Event("omi:navigate"));
    return;
  }
  if (replace) window.history.replaceState(null, "", path);
  else window.history.pushState(null, "", path);
  window.dispatchEvent(new Event("omi:navigate"));
}

export function subscribeNavigation(listener: () => void): () => void {
  window.addEventListener("popstate", listener);
  window.addEventListener("omi:navigate", listener);
  return () => {
    window.removeEventListener("popstate", listener);
    window.removeEventListener("omi:navigate", listener);
  };
}

export function initializeNavigation(): () => void {
  const legacy = legacyRoutePath(window.location.pathname, window.location.hash);
  if (legacy) window.history.replaceState(null, "", `${legacy}${window.location.search}`);

  // Keep ordinary internal links in the app, while preserving new-tab clicks,
  // downloads, external links, and document anchors as native browser actions.
  const click = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = event.target instanceof Element ? event.target.closest("a") : null;
    if (!anchor || anchor.hasAttribute("download") || (anchor.target && anchor.target !== "_self")) return;
    const url = new URL(anchor.href);
    if (url.origin !== window.location.origin || url.hash || !isAppPath(url.pathname)) return;
    event.preventDefault();
    navigatePath(`${url.pathname}${url.search}`);
  };
  document.addEventListener("click", click);
  return () => document.removeEventListener("click", click);
}
