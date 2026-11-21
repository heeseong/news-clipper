// Administration (M9): program profiles, storage and retention, operating metrics.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { CleanupItem, MetricsSummary, Program, ProgramPatch, StorageInfo } from "./types";

export function usePatchProgram(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: ProgramPatch) => api.patch<Program>(`/api/programs/${id}`, patch),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["programs"] }),
  });
}

export function useProgramOverlay(id: number) {
  const qc = useQueryClient();
  const done = () => void qc.invalidateQueries({ queryKey: ["programs"] });
  return {
    upload: useMutation({ mutationFn: (file: File) => api.put<Program>(`/api/programs/${id}/thumbnail-overlay`, file), onSuccess: done }),
    remove: useMutation({ mutationFn: () => api.delete<Program>(`/api/programs/${id}/thumbnail-overlay`), onSuccess: done }),
  };
}

export function useStorage() {
  return useQuery({ queryKey: ["admin", "storage"], queryFn: () => api.get<StorageInfo>("/api/admin/storage") });
}

export function useRunCleanup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ deleted: CleanupItem[]; bytes: number; errors: string[] }>("/api/admin/cleanup"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin"] }),
  });
}

export function useMetrics(days: number) {
  return useQuery({ queryKey: ["admin", "metrics", days], queryFn: () => api.get<MetricsSummary>(`/api/admin/metrics?days=${days}`) });
}
