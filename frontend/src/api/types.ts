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
  clip_counts: { total: number; ranged: number; approved: number; rejected: number };
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
  dedupe_program: string | null;
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
  briefs: number;
  uploads: number;
  aired: number;
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
  dedupe_note: string | null;
  title: string;
  title_raw: string | null;
  series: string | null;
  reporter: string | null;
  cue_sheet_reporter: string | null;
  planned_duration: number | null;
  estimated_duration: number | null;
  remark: string | null;
  parsed_script: ParsedScript | null;
  parse_warnings: string[];
  align_status: "ALIGNED" | "NOT_FOUND" | "NO_SCRIPT" | null;
  aligned_start: number | null;
  aligned_end: number | null;
  align_score: number | null;
  align_flags: string[];
}

export interface Clip {
  id: number;
  job_id: number;
  rundown_item_id: number;
  item_no: number;
  title: string;
  reporter: string | null;
  planned_duration: number | null;
  status: string;
  start_sec: number | null;
  end_sec: number | null;
  start_snap: string | null;
  end_snap: string | null;
  suggested_start: number | null;
  suggested_end: number | null;
  confidence: number | null;
  flags: string[];
  needs_manual_range: boolean;
  youtube_title: string | null;
  thumbnail_url: string | null;
  warnings_acknowledged: boolean;
  edited_at: string | null;
  reviewed_at: string | null;
  reject_reason: string | null;
  kind: "report" | "brief";
  locked_by_name: string | null;
}

export interface TextCandidate {
  text: string;
  source: "script" | "title" | "llm";
  warnings: string[];
}

export interface ClipMetadata {
  title: string;
  title_candidates: TextCandidate[];
  description: string;
  summary: string | null;
  tags: string[];
  hashtags: string[];
  thumbnail_text: string;
  thumbnail_text_candidates: TextCandidate[];
  privacy: string;
  publish_at: string | null;
  channel_ids: number[];
  llm_status: "ok" | "failed" | "off";
  llm_provider: string | null;
  llm_model: string | null;
  llm_input_tokens: number;
  llm_output_tokens: number;
  warnings: string[];
  generated_at: string | null;
}

export interface ThumbnailCandidate {
  id: number;
  source: string;
  rank: number;
  frame_sec: number | null;
  score: number | null;
  details: Record<string, number>;
  overlay_text: string | null;
  selected: boolean;
  url: string;
}

export interface ClipDetail extends Clip {
  metadata: ClipMetadata | null;
  thumbnails: ThumbnailCandidate[];
  approval_problems: string[];
  lock_expires_at: string | null;
}

export interface AlignedSentence {
  seq: number;
  section: string;
  kind: string;
  text: string;
  start_sec: number;
  end_sec: number;
  score: number;
}

export interface ClipPatch {
  start_sec?: number;
  end_sec?: number;
  title?: string;
  description?: string;
  tags?: string[];
  hashtags?: string[];
  thumbnail_text?: string;
  privacy?: string;
  publish_at?: string | null;
  clear_publish_at?: boolean;
  warnings_acknowledged?: boolean;
}

export interface BulkResult {
  clip_id: number;
  ok: boolean;
  error: string | null;
  problems: string[];
  publish_at: string | null;
}

export interface AuditEntry {
  id: number;
  user_name: string | null;
  action: string;
  diff: Record<string, unknown>;
  created_at: string;
}

export interface Waveform {
  rate: number;
  peaks: number[];
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
