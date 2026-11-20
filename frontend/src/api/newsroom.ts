import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { JobDetail, Rundown, RundownPreview } from "./types";

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
