// Review: one clip's editor data and actions (M5). Mutations update the clip query in place.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError, api } from "./client";
import type { AlignedSentence, AuditEntry, BulkResult, ClipDetail, ClipPatch, Waveform } from "./types";

export const clipKey = (id: number) => ["clip", id] as const;

export function useClipDetail(id: number) {
  return useQuery({
    queryKey: clipKey(id),
    queryFn: () => api.get<ClipDetail>(`/api/clips/${id}`),
    // Follow the final render after approval (M6).
    // Follow the render and the upload (M7) after approval.
    refetchInterval: (q) => {
      const c = q.state.data;
      if (!c) return false;
      if (["APPROVED", "RENDERING"].includes(c.status) && !c.render_error) return 2000;
      if (["UPLOADING", "UPLOADED", "PROCESSING"].includes(c.status)) return 3000;
      if (c.status === "SCHEDULED") return 60_000;
      return false;
    },
  });
}

export function useSentences(id: number, rangeVersion: string) {
  return useQuery({
    queryKey: ["clip", id, "sentences", rangeVersion],
    queryFn: () => api.get<AlignedSentence[]>(`/api/clips/${id}/sentences`),
    staleTime: Infinity,
  });
}

export function useWaveform(jobId: number) {
  return useQuery({
    queryKey: ["jobs", jobId, "waveform"],
    queryFn: () => api.get<Waveform>(`/media/jobs/${jobId}/waveform.json`),
    staleTime: Infinity,
    retry: false,
  });
}

export function useAudit(id: number, version: string) {
  return useQuery({
    queryKey: ["clip", id, "audit", version],
    queryFn: () => api.get<AuditEntry[]>(`/api/clips/${id}/audit`),
  });
}

function useClipMutation<V>(id: number, fn: (v: V) => Promise<ClipDetail>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (clip) => {
      qc.setQueryData(clipKey(id), clip);
      void qc.invalidateQueries({ queryKey: ["jobs", clip.job_id] });
      void qc.invalidateQueries({ queryKey: ["clip", id, "audit"] });
    },
  });
}

export function usePatchClip(id: number) {
  return useClipMutation(id, (patch: ClipPatch) => api.patch<ClipDetail>(`/api/clips/${id}`, patch));
}

export function useClipAction(id: number) {
  return useClipMutation(id, (a: { action: "approve" | "reopen" | "render" } | { action: "reject"; reason: string }) =>
    api.post<ClipDetail>(`/api/clips/${id}/${a.action}`, a.action === "reject" ? { reason: a.reason } : undefined),
  );
}

export function useThumbnailActions(id: number) {
  return {
    select: useClipMutation(id, (thumbId: number) =>
      api.post<ClipDetail>(`/api/clips/${id}/thumbnails/${thumbId}/select`),
    ),
    frame: useClipMutation(id, (frame_sec: number) =>
      api.post<ClipDetail>(`/api/clips/${id}/thumbnails`, { frame_sec }),
    ),
    upload: useClipMutation(id, (file: File) => api.put<ClipDetail>(`/api/clips/${id}/thumbnails/upload`, file)),
  };
}

export function useBulk() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      clip_ids: number[];
      action: "approve" | "schedule" | "publish";
      first_at?: string;
      interval_minutes?: number;
      privacy?: string;
    }) => api.post<BulkResult[]>("/api/clips/bulk", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      void qc.invalidateQueries({ queryKey: ["clip"] });
    },
  });
}

// Lock requests per clip run one after another, so a release from a closing editor (or React's
// double mount in development) can never land after the next editor's lock.
const lockChain = new Map<number, Promise<unknown>>();
function chained<T>(id: number, fn: () => Promise<T>): Promise<T> {
  const next = (lockChain.get(id) ?? Promise.resolve()).then(fn, fn);
  lockChain.set(id, next.catch(() => undefined));
  return next;
}

/** Hold the clip's edit lock while the editor is open (refreshed every 30 s, released on leave).
 * Returns the name of another reviewer holding it, if any. */
export function useClipLock(id: number, enabled: boolean): string | null {
  const [holder, setHolder] = useState<string | null>(null);
  const qc = useQueryClient();
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    const take = () =>
      chained(id, () => api.post<ClipDetail>(`/api/clips/${id}/lock`))
        .then((clip) => {
          if (!stopped) {
            setHolder(null);
            qc.setQueryData(clipKey(id), clip);
          }
        })
        .catch((e: unknown) => {
          if (!stopped && e instanceof ApiError && e.status === 423) setHolder(e.message);
        });
    void take();
    const timer = window.setInterval(take, 30_000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      void chained(id, () => api.delete(`/api/clips/${id}/lock`)).catch(() => undefined);
    };
  }, [id, enabled, qc]);
  return holder;
}

/** The job's clips for the editor's previous/next navigation and the bulk actions. */
export function useClipList(jobId: number) {
  return useQuery({
    queryKey: ["jobs", jobId, "clips-review"],
    queryFn: () => api.get<import("./types").Clip[]>(`/api/jobs/${jobId}/clips`),
  });
}
