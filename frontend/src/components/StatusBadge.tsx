import type { JobStatus, StepStatus } from "../api/types";
import { JOB_STATUS_LABEL, STEP_STATUS_LABEL, tone } from "../labels";

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return <span className={`badge badge-${tone(status)}`}>{JOB_STATUS_LABEL[status]}</span>;
}

export function StepStatusBadge({ status }: { status: StepStatus }) {
  return <span className={`badge badge-${tone(status)}`}>{STEP_STATUS_LABEL[status]}</span>;
}
