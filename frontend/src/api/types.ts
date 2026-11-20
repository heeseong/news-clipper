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
  | "WAITING"
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
  cue_sheet_id: number | null;
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
  cue_sheet_id?: number | null;
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
  excluded_sources: string[];
}

// --- newsroom (backend/app/schemas/newsroom.py) ---

export interface RundownSummary {
  cue_sheet_id: number;
  program_name: string;
  program_type: string | null;
  broadcast_date: string | null;
  broadcast_start_time: string | null;
  version: number;
  anchors: string[];
  updated_at: string | null;
}

export interface RundownCounts {
  items: number;
  reports: number;
  uploads: number;
  excluded: number;
  planned_total_seconds: number | null;
}

export interface RundownPreview {
  broadcast_date: string;
  program_name: string | null;
  matched: RundownSummary | null;
  reason: string;
  ambiguous: boolean;
  candidates: RundownSummary[];
  day_rundowns: RundownSummary[];
  counts: RundownCounts | null;
  warnings: string[];
}

export interface ScriptSentence {
  section: "lead" | "report";
  kind: string;
  text: string;
  speaker: string | null;
}

export interface ParsedScript {
  structured: boolean;
  sentences: ScriptSentence[];
  signoff_reporter: string | null;
  warnings: string[];
  syllable_count: number;
}

export interface RundownItem {
  id: number;
  item_no: number;
  type: "R" | "S" | "OTHER";
  type_reason: string | null;
  source: string | null;
  source_reason: string | null;
  upload: boolean;
  title: string;
  title_raw: string | null;
  series: string | null;
  reporter: string | null;
  planned_duration: number | null;
  estimated_duration: number | null;
  remark: string | null;
  parsed_script: ParsedScript | null;
  parse_warnings: string[];
}

export interface RundownChange {
  at: string;
  from: number;
  to: number;
  added: { item_no: number; title: string }[];
  removed: { item_no: number; title: string }[];
  moved: { title: string; from: number; to: number }[];
  edited: { item_no: number; title: string }[];
  cue_sheet?: { from: number; to: number };
}

export interface Rundown {
  id: number;
  job_id: number;
  newsroom_rundown_id: number;
  program_name: string;
  broadcast_date: string | null;
  broadcast_start_time: string | null;
  edition: number;
  confirmed_at: string | null;
  fetched_at: string;
  match_reason: string | null;
  changes: RundownChange[];
  warnings: string[];
  items: RundownItem[];
  counts: RundownCounts;
}
