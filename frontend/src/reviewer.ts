// The reviewer's name, sent with every request (X-Reviewer) until login exists (M0.7).
import { useSyncExternalStore } from "react";

const KEY = "news-clipper.reviewer";
const listeners = new Set<() => void>();

export function getReviewer(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function setReviewer(name: string) {
  try {
    localStorage.setItem(KEY, name.trim());
  } catch {
    // private mode: the name lives for this page only
  }
  listeners.forEach((l) => l());
}

export function useReviewer(): string {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    getReviewer,
  );
}
