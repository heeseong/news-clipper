import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import { useLive } from "./events";
import type {
  Analysis,
  DummyJobRequest,
  Job,
  JobCreateRequest,
  JobDetail,
  JobList,
  JobStatus,
  Program,
  Stage,
} from "./types";

const ACTIVE: JobStatus[] = ["UPLOADING", "UPLOADED", "PROCESSING", "ALIGNING", "GENERATING"];
export const isActive = (job: Job) => ACTIVE.includes(job.status);

// Updates arrive over SSE; poll only while the event stream is down.
function fallbackInterval(live: boolean, active: boolean): number | false {
  if (live) return false;
  return active ? 2000 : 15000;
}

export function useJobs() {
  const live = useLive();
  return useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.get<JobList>("/api/jobs"),
    refetchInterval: (q) => fallbackInterval(live, !!q.state.data?.items.some(isActive)),
  });
}

export function useJob(id: number) {
  const live = useLive();
  return useQuery({
    queryKey: ["jobs", id],
    queryFn: () => api.get<JobDetail>(`/api/jobs/${id}`),
    refetchInterval: (q) => fallbackInterval(live, !q.state.data || isActive(q.state.data)),
  });
}

/** Scene cuts and silences. `version` changes when the analyze step's outputs change. */
export function useAnalysis(id: number, version: string | null) {
  return useQuery({
    queryKey: ["jobs", id, "analysis", version],
    queryFn: () => api.get<Analysis>(`/api/jobs/${id}/analysis`),
    enabled: version !== null,
    staleTime: Infinity,
  });
}

export function usePipeline() {
  return useQuery({
    queryKey: ["pipeline"],
    queryFn: () => api.get<Stage[]>("/api/pipeline"),
    staleTime: Infinity,
  });
}

export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JobCreateRequest) => api.post<JobDetail>("/api/jobs", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useCreateDummyJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DummyJobRequest) => api.post<JobDetail>("/api/jobs/dummy", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRetryStep(jobId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (step: string) => api.post<JobDetail>(`/api/jobs/${jobId}/steps/${step}/retry`),
    onSuccess: (job) => {
      qc.setQueryData(["jobs", jobId], job);
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function usePrograms() {
  return useQuery({ queryKey: ["programs"], queryFn: () => api.get<Program[]>("/api/programs") });
}

export function useCreateProgram() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.post<Program>("/api/programs", { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["programs"] }),
  });
}
