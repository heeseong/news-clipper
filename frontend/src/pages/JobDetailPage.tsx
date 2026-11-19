import { Link, useParams } from "react-router-dom";

import { useJob, usePipeline, useRetryStep } from "../api/jobs";
import type { JobStep } from "../api/types";
import { JobStatusBadge, StepStatusBadge } from "../components/StatusBadge";
import { STAGE_LABEL, STEP_LABEL, formatDateTime, formatSeconds } from "../labels";

export function JobDetailPage() {
  const id = Number(useParams().id);
  const job = useJob(id);
  const pipeline = usePipeline();
  const retry = useRetryStep(id);

  if (job.isPending) return <p className="muted">불러오는 중…</p>;
  if (job.isError) return <p className="error-box">작업을 불러오지 못했습니다: {job.error.message}</p>;

  const data = job.data;
  const stages = pipeline.data ?? [];

  return (
    <section>
      <p>
        <Link to="/jobs">← 작업 목록</Link>
      </p>
      <div className="page-head">
        <h1>
          작업 #{data.id} {data.is_dummy && <span className="tag">더미</span>}
        </h1>
        <JobStatusBadge status={data.status} />
      </div>
      <dl className="meta">
        <dt>프로그램</dt>
        <dd>{data.program_name ?? "—"}</dd>
        <dt>방송일</dt>
        <dd>{data.broadcast_date ?? "—"}</dd>
        <dt>생성</dt>
        <dd>{formatDateTime(data.created_at)}</dd>
      </dl>
      {data.error && <p className="error-box">{data.error}</p>}

      <div className="stages">
        {stages.map((stage, i) => (
          <div key={stage.name} className="stage">
            <h2>
              {i + 1}. {STAGE_LABEL[stage.name] ?? stage.name}
              {stage.steps.length > 1 && <span className="muted small"> (병렬)</span>}
            </h2>
            {data.steps
              .filter((s) => s.stage === i)
              .map((s) => (
                <StepCard
                  key={s.step}
                  step={s}
                  onRetry={() => retry.mutate(s.step)}
                  retrying={retry.isPending}
                />
              ))}
          </div>
        ))}
      </div>
      {retry.isError && <p className="error-box">재시도하지 못했습니다: {retry.error.message}</p>}
    </section>
  );
}

function StepCard({ step, onRetry, retrying }: { step: JobStep; onRetry: () => void; retrying: boolean }) {
  return (
    <div className={`step step-${step.status.toLowerCase()}`}>
      <div className="step-head">
        <strong>{STEP_LABEL[step.step] ?? step.step}</strong>
        <StepStatusBadge status={step.status} />
      </div>
      <div className="muted small">
        큐 {step.queue} · 시도 {step.attempts}회 · {formatSeconds(step.started_at, step.finished_at)}
      </div>
      {step.error && <div className="error-text small">{step.error}</div>}
      {step.status === "FAILED" && (
        <button className="btn btn-small" onClick={onRetry} disabled={retrying}>
          이 단계부터 재시도
        </button>
      )}
    </div>
  );
}
