// Server-sent events (GET /api/events): the server says "job N changed", we refetch.
// While the stream is down, queries fall back to polling (see useLive()).

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useSyncExternalStore } from "react";

let live = false;
const listeners = new Set<() => void>();

function setLive(value: boolean) {
  if (live === value) return;
  live = value;
  listeners.forEach((l) => l());
}

/** True while the event stream is connected. */
export function useLive(): boolean {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => live,
  );
}

/** Mount once (Layout). Refetches job queries when the server reports a change. */
export function useJobEvents() {
  const qc = useQueryClient();
  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onopen = () => setLive(true);
    source.onerror = () => setLive(false); // EventSource reconnects by itself
    source.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as { type: string; id: number };
        if (event.type === "job") {
          qc.invalidateQueries({ queryKey: ["jobs", event.id], exact: true });
          qc.invalidateQueries({ queryKey: ["jobs"], exact: true });
        }
      } catch {
        // ignore malformed events
      }
    };
    return () => {
      source.close();
      setLive(false);
    };
  }, [qc]);
}
