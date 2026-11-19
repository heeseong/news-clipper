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
  FAILED: "실패",
};

export const STAGE_LABEL: Record<string, string> = {
  processing: "수집·분석",
  aligning: "정렬",
  generating: "메타·썸네일",
};

export const STEP_LABEL: Record<string, string> = {
  proxy: "프록시 변환",
  analyze: "오디오·신호 분석",
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
