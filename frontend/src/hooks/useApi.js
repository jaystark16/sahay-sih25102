import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Data fetching with explicit loading / error / empty states.
 *
 * The states are kept separate on purpose. A page that cannot tell "still
 * loading" from "loaded, and there is genuinely nothing here" from "the
 * request failed" will render one of them as the others, which in this product
 * means showing a mentor an empty worklist when the server is down.
 *
 * In-flight requests are aborted when the inputs change or the component
 * unmounts, so a slow response cannot land after a newer one and overwrite it.
 */
export function useApi(fetcher, deps = [], { enabled = true, initial = null } = {}) {
  const [data, setData] = useState(initial);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(enabled);
  const abortRef = useRef(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const run = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const result = await fetcherRef.current({ signal: controller.signal });
      if (!controller.signal.aborted) setData(result);
    } catch (e) {
      // A cancelled request is not a failure and must not clear the screen.
      if (e?.name === 'AbortError' || controller.signal.aborted) return;
      setError(e);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) { setLoading(false); return undefined; }
    run();
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);

  return { data, error, loading, reload: run, setData };
}

/**
 * A one-shot action (submit, delete, refresh) with its own busy/error state.
 * Separate from useApi because an action's failure should not blank the page
 * that triggered it.
 */
export function useAction(fn) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const execute = useCallback(async (...args) => {
    setBusy(true);
    setError(null);
    try {
      return await fn(...args);
    } catch (e) {
      setError(e);
      throw e;
    } finally {
      setBusy(false);
    }
  }, [fn]);

  return { execute, busy, error, clearError: () => setError(null) };
}

/** Debounce, so a search box does not fire a request per keystroke. */
export function useDebounced(value, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}
