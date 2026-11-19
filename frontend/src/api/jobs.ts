import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { DummyJobRequest, Job, JobDetail, JobList, JobStatus, Stage } from "./types";

// Polling until progress is pushed over SSE (M1 1.8).
const ACTIVE: JobStatus[] = ["UPLOADING", "UPLOADED", "PROCESSING", "ALIGNING", "GENERATING"];
export const isActive = (job: Job) => ACTIVE.includes(job.status);

export function useJobs() {
  return useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.get<JobList>("/api/jobs"),
    refetchInterval: (q) => (q.state.data?.items.some(isActive) ? 1000 : 5000),
  });
}

export function useJob(id: number) {
  return useQuery({
    queryKey: ["jobs", id],
    queryFn: () => api.get<JobDetail>(`/api/jobs/${id}`),
    refetchInterval: (q) => (q.state.data && !isActive(q.state.data) ? false : 1000),
  });
}

export function usePipeline() {
  return useQuery({
    queryKey: ["pipeline"],
    queryFn: () => api.get<Stage[]>("/api/pipeline"),
    staleTime: Infinity,
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
