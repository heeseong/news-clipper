import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { Clip, ClipDetail, JobDetail, Rundown, RundownPreview } from "./types";

/** Which cue sheet a recording of this program and date would be matched to (upload form). */
export function useRundownPreview(programId: number | null, date: string, cueSheetId: number | null, enabled = true) {
  const params = new URLSearchParams({ date });
  if (programId != null) params.set("program_id", String(programId));
  if (cueSheetId != null) params.set("cue_sheet_id", String(cueSheetId));
  return useQuery({
    queryKey: ["newsroom", "preview", programId, date, cueSheetId],
    queryFn: () => api.get<RundownPreview>(`/api/newsroom/rundowns?${params}`),
    enabled: enabled && !!date,
    staleTime: 30_000,
    retry: false,
  });
}

/** The job's stored cue sheet. `version` changes whenever the newsroom step finishes again. */
export function useJobRundown(jobId: number, version: string | null) {
  return useQuery({
    queryKey: ["jobs", jobId, "rundown", version],
    queryFn: () => api.get<Rundown>(`/api/jobs/${jobId}/rundown`),
    enabled: version !== null,
    staleTime: Infinity,
  });
}

export function useRefreshRundown(jobId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cueSheetId: number | null) =>
      api.post<JobDetail>(`/api/jobs/${jobId}/rundown/refresh`, { cue_sheet_id: cueSheetId }),
    onSuccess: (job) => {
      qc.setQueryData(["jobs", jobId], job);
      qc.invalidateQueries({ queryKey: ["jobs"], exact: true });
    },
  });
}

/** Report clips made by the align step. `version` changes whenever that step finishes again. */
export function useJobClips(jobId: number, version: string | null) {
  return useQuery({
    queryKey: ["jobs", jobId, "clips", version],
    queryFn: () => api.get<Clip[]>(`/api/jobs/${jobId}/clips`),
    enabled: version !== null,
    staleTime: Infinity,
  });
}

/** One clip with its metadata and thumbnail candidates. */
export function useClip(clipId: number | null, version: string | null, poll = false) {
  return useQuery({
    queryKey: ["clips", clipId, version],
    queryFn: () => api.get<ClipDetail>(`/api/clips/${clipId}`),
    enabled: clipId != null && version !== null,
    staleTime: Infinity,
    refetchInterval: poll ? 2000 : false,
  });
}

/** LLM candidates again (plan 4.6). The job event stream announces the result. */
export function useRegenerateMetadata(clipId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ queued: boolean }>(`/api/clips/${clipId}/metadata/regenerate`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["clips", clipId] });
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
