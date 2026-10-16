import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, errorMessage, request } from "./api";

export function useResource<T>(path: string | null, interval = 0) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(path));
  const [revision, setRevision] = useState(0);
  const loadedPath = useRef(path);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    if (!path) {
      setData(undefined);
      setError("");
      setLoading(false);
      return;
    }
    if (loadedPath.current !== path) {
      setData(undefined);
      loadedPath.current = path;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    request<T>(path, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(error));
          if (error instanceof ApiError && error.status === 404)
            setData(undefined);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, revision]);
  useEffect(() => {
    if (!interval) return;
    const timer = window.setInterval(refresh, interval);
    const onFocus = () => refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [interval, refresh]);
  return {
    data: loadedPath.current === path ? data : undefined,
    setData,
    error: loadedPath.current === path ? error : "",
    loading: loading || Boolean(path && loadedPath.current !== path),
    refresh,
  };
}
