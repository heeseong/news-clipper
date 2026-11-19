import type { JobStatus, StepStatus } from "./api/types";

export const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  UPLOADING: "업로드 중",
  UPLOADED: "업로드 완료",
  WAITING_RUNDOWN: "큐시트 대기",
  PROCESSING: "분석 중",
  ALIGNING: "정렬 중",
  GENERATING: "메타 생성 중",
  REVIEW_READY: "검수 대기",
  DONE: "완료",
  FAILED: "실패",
};

export const STEP_STATUS_LABEL: Record<StepStatus, string> = {
  PENDING: "대기",
  QUEUED: "큐 대기",
  RUNNING: "실행 중",
  RETRYING: "재시도 예정",
  SUCCEEDED: "완료",
  SKIPPED: "미구현",
  FAILED: "실패",
};

export const STAGE_LABEL: Record<string, string> = {
  processing: "수집·분석",
  aligning: "정렬",
  generating: "메타·썸네일",
};

export const STEP_LABEL: Record<string, string> = {
  proxy: "프록시·장면 전환",
  analyze: "정렬용 오디오·무음",
  newsroom: "큐시트·원고",
  align: "원고 정렬",
  metadata: "메타데이터",
  thumbnails: "썸네일",
};

export function tone(status: JobStatus | StepStatus): "ok" | "busy" | "warn" | "error" | "idle" {
  switch (status) {
    case "REVIEW_READY":
    case "DONE":
    case "SUCCEEDED":
      return "ok";
    case "FAILED":
      return "error";
    case "RETRYING":
    case "WAITING_RUNDOWN":
      return "warn";
    case "PENDING":
    case "UPLOADED":
    case "SKIPPED":
      return "idle";
    default:
      return "busy";
  }
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", hour12: false });
}

export function formatSeconds(start: string | null, end: string | null): string {
  if (!start || !end) return "—";
  return `${((new Date(end).getTime() - new Date(start).getTime()) / 1000).toFixed(1)}초`;
}

/** HH:MM:SS:FF (frames), the broadcast timecode style used on screen (spec 7). */
export function timecode(seconds: number, fps: number | null): string {
  const rate = fps && fps > 0 ? fps : 30;
  const nominal = Math.round(rate);
  const totalFrames = Math.round(seconds * rate);
  const frames = totalFrames % nominal;
  const totalSeconds = Math.floor(totalFrames / rate + 1e-6);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}:${pad(frames)}`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}시간 ${m % 60}분 ${s % 60}초` : `${m}분 ${s % 60}초`;
}

export function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** Today's date in Korea as YYYY-MM-DD (default broadcast date). */
export function todayKst(): string {
  return new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
}
