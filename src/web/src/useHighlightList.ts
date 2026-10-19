import { useEffect, useState } from "react";
import { errorMessage, request } from "./api";
import { profileScope } from "./profiles";
import { useResource } from "./useResource";

// All categories must see the full selected day before grouping or matching a topic.
export function useHighlightList<R extends { items: { id: string }[]; total: number }>(path: string, changedEvent: string) {
  type Item = R["items"][number];
  const resource = useResource<R>(path, 15_000);
  const [remaining, setRemaining] = useState<{ path: string; items: Item[]; loading: boolean; error: string }>();
  useEffect(() => {
    const first = resource.data;
    if (!first || first.total <= first.items.length) { setRemaining(undefined); return; }
    const controller = new AbortController(), scope = profileScope();
    setRemaining({ path, items: [], loading: true, error: "" });
    void (async () => {
      const items: Item[] = [];
      try {
        let offset = first.items.length;
        while (offset < first.total) {
          const [route, query] = path.split("?");
          const params = new URLSearchParams(query); params.set("offset", String(offset));
          const next = await request<R>(`${route}?${params}`, { signal: controller.signal }, scope);
          if (!next.items.length) break;
          items.push(...next.items); offset += next.items.length;
        }
        if (!controller.signal.aborted && !scope.signal.aborted) setRemaining({ path, items, loading: false, error: "" });
      } catch (error) {
        if (!controller.signal.aborted && !scope.signal.aborted) setRemaining({ path, items: [], loading: false, error: errorMessage(error) });
      }
    })();
    return () => controller.abort();
  }, [path, resource.data]);
  useEffect(() => {
    const invalidate = () => { resource.setData(undefined); setRemaining(undefined); resource.refresh(); };
    window.addEventListener("omi:archive-changed", invalidate);
    window.addEventListener(changedEvent, resource.refresh);
    return () => {
      window.removeEventListener("omi:archive-changed", invalidate);
      window.removeEventListener(changedEvent, resource.refresh);
    };
  }, [changedEvent, resource.refresh, resource.setData]);
  const extra = remaining?.path === path ? remaining : undefined;
  const items: Item[] = [...(resource.data?.items || []), ...(extra?.items || [])];
  return { ...resource, items: [...new Map(items.map((item) => [item.id, item])).values()],
    loadingMore: extra?.loading || false, error: resource.error || extra?.error || "" };
}
