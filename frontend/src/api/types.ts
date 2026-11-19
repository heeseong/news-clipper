// Mirrors backend/app/schemas/*.py.

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

export type StepStatus =
  | "PENDING"
  | "QUEUED"
  | "RUNNING"
  | "RETRYING"
  | "SUCCEEDED"
  | "SKIPPED"
  | "FAILED";

export interface JobStep {
  step: string;
  stage: number;
  queue: string;
  status: StepStatus;
  attempts: number;
  progress: number | null;
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
  fps: number | null;
  width: number | null;
  height: number | null;
  source_name: string | null;
  source_size: number | null;
  warnings: string[];
  is_dummy: boolean;
  created_at: string;
  updated_at: string;
}

export interface Artifacts {
  proxy: boolean;
  sprite: boolean;
  waveform: boolean;
  scenes: boolean;
  silences: boolean;
}

export interface JobDetail extends Job {
  steps: JobStep[];
  artifacts: Artifacts;
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

export interface JobCreateRequest {
  upload_id: string;
  program_id: number | null;
  broadcast_date: string;
}

export interface SceneCut {
  time: number;
  frame: number;
  score: number;
}

export interface SilenceRange {
  start: number;
  end: number;
}

export interface Analysis {
  scenes: SceneCut[] | null;
  silences: SilenceRange[] | null;
  scene_threshold: number | null;
}

export interface Program {
  id: number;
  name: string;
  broadcast_time: string | null;
  audio_track: number | null;
}
