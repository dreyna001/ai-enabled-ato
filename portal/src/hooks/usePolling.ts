import { useEffect, useRef } from "react";

export function usePolling(
  callback: () => void | Promise<void>,
  {
    enabled,
    intervalMs = 2000,
  }: {
    enabled: boolean;
    intervalMs?: number;
  },
) {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    if (!enabled) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void callbackRef.current();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [enabled, intervalMs]);
}

export function useAbortableEffect(
  effect: (signal: AbortSignal) => void | Promise<void>,
  deps: unknown[],
) {
  useEffect(() => {
    const controller = new AbortController();
    void effect(controller.signal);
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
