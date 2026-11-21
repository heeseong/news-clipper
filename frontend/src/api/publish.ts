// Channels and uploads (M7).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { ChannelList, ClipDetail, ConnectState, Program, PublishSummary, PublishTask } from "./types";
import { clipKey } from "./review";

export function useChannels() {
  return useQuery({ queryKey: ["channels"], queryFn: () => api.get<ChannelList>("/api/channels") });
}

export function useConnectChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mode: "fake" | "web" | "device") => api.post<ConnectState>("/api/channels/connect", { mode }),
    onSuccess: (s) => {
      if (s.status === "connected") void qc.invalidateQueries({ queryKey: ["channels"] });
    },
  });
}

export function pollConnect(sessionId: string) {
  return api.post<ConnectState>(`/api/channels/connect/${sessionId}/poll`);
}

export function useDisconnectChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete(`/api/channels/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["channels"] }),
  });
}

export function useSetProgramChannels() {
  const qc = useQueryClient();
  return useMutation({
    // PUT replaces the whole profile: send it back with only the channels changed.
    mutationFn: ({ program, channels }: { program: Program; channels: number[] }) =>
      api.putJson<Program>(`/api/programs/${program.id}`, { ...program, default_channels: channels }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["programs"] }),
  });
}

const ACTIVE = ["QUEUED", "UPLOADING", "UPLOADED", "PROCESSING"];

export function usePublishTasks(filter: { status?: string; channel_id?: number }) {
  const params = new URLSearchParams();
  if (filter.status) for (const s of filter.status.split(",")) params.append("status", s);
  if (filter.channel_id) params.set("channel_id", String(filter.channel_id));
  return useQuery({
    queryKey: ["publish-tasks", filter],
    queryFn: () => api.get<PublishTask[]>(`/api/publish-tasks?${params}`),
    refetchInterval: (q) => (q.state.data?.some((t) => ACTIVE.includes(t.status)) ? 3000 : 30_000),
  });
}

export function usePublishSummary() {
  return useQuery({ queryKey: ["publish-summary"], queryFn: () => api.get<PublishSummary>("/api/publish/summary"), refetchInterval: 30_000 });
}

export function useTaskAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: number; action: "retry" | "cancel" | "check" }) => api.post<PublishTask>(`/api/publish-tasks/${id}/${action}`),
    onSuccess: (t) => {
      void qc.invalidateQueries({ queryKey: ["publish-tasks"] });
      void qc.invalidateQueries({ queryKey: ["publish-summary"] });
      void qc.invalidateQueries({ queryKey: clipKey(t.clip_id) });
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function usePublishClip(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ClipDetail>(`/api/clips/${id}/publish`),
    onSuccess: (clip) => {
      qc.setQueryData(clipKey(id), clip);
      void qc.invalidateQueries({ queryKey: ["jobs", clip.job_id] });
      void qc.invalidateQueries({ queryKey: ["publish-tasks"] });
    },
  });
}
