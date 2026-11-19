// Mirrors backend/app/schemas/job.py.

export type JobStatus =
  | "UPLOADING"
  | "UPLOADED"
  | "WAITING_RUNDOWN"
  | "PROCESSING"
  | "ALIGNING"
  | "GENERATING"
  | "REVIEW_READY"
  | "DONE"
  | "FAILED";

export type StepStatus = "PENDING" | "QUEUED" | "RUNNING" | "RETRYING" | "SUCCEEDED" | "FAILED";

export interface JobStep {
  step: string;
  stage: number;
  queue: string;
  status: StepStatus;
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface Job {
  id: number;
  program_id: number | null;
  program_name: string | null;
  broadcast_date: string | null;
  status: JobStatus;
  current_step: string | null;
  error: string | null;
  duration: number | null;
  is_dummy: boolean;
  created_at: string;
  updated_at: string;
}

export interface JobDetail extends Job {
  steps: JobStep[];
}

export interface JobList {
  items: Job[];
  total: number;
}

export interface Stage {
  name: string;
  status: JobStatus;
  steps: string[];
}

export interface DummyJobRequest {
  seconds?: number;
  fail_steps?: string[];
  flaky_steps?: string[];
}
