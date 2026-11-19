import { useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useAnalysis, useJob, usePipeline, useRetryStep } from "../api/jobs";
import type { JobDetail, JobStep } from "../api/types";
import { JobStatusBadge, StepStatusBadge } from "../components/StatusBadge";
import {
  STAGE_LABEL,
  STEP_LABEL,
  formatBytes,
  formatDateTime,
  formatDuration,
  formatSeconds,
  timecode,
} from "../labels";

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
        {data.source_name && (
          <>
            <dt>원본</dt>
            <dd>
              {data.source_name} · {formatBytes(data.source_size)} · {formatDuration(data.duration)}
              {data.width && ` · ${data.width}×${data.height}`}
              {data.fps && ` · ${data.fps.toFixed(2)}fps`}
            </dd>
          </>
        )}
        <dt>생성</dt>
        <dd>{formatDateTime(data.created_at)}</dd>
      </dl>
      {data.warnings.length > 0 && (
        <ul className="warn-box">
          {data.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
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

      {!data.is_dummy && <MediaPanel job={data} />}
    </section>
  );
}

function StepCard({ step, onRetry, retrying }: { step: JobStep; onRetry: () => void; retrying: boolean }) {
  const running = step.status === "RUNNING" && step.progress != null;
  return (
    <div className={`step step-${step.status.toLowerCase()}`}>
      <div className="step-head">
        <strong>{STEP_LABEL[step.step] ?? step.step}</strong>
        <StepStatusBadge status={step.status} />
      </div>
      {running && (
        <div className="bar bar-thin" title={`${Math.round(step.progress! * 100)}%`}>
          <div className="bar-fill" style={{ width: `${step.progress! * 100}%` }} />
        </div>
      )}
      <div className="muted small">
        큐 {step.queue} · 시도 {step.attempts}회 · {running ? `${Math.round(step.progress! * 100)}%` : formatSeconds(step.started_at, step.finished_at)}
      </div>
      {step.error && (
        <div className={`small ${step.status === "SKIPPED" ? "muted" : "error-text"}`}>{step.error}</div>
      )}
      {step.status === "FAILED" && (
        <button className="btn btn-small" onClick={onRetry} disabled={retrying}>
          이 단계부터 재시도
        </button>
      )}
    </div>
  );
}

/** Proxy player plus the cuts and silences found by the analyze step (review UI comes in M5). */
function MediaPanel({ job }: { job: JobDetail }) {
  const video = useRef<HTMLVideoElement>(null);
  const [tab, setTab] = useState<"cuts" | "silences">("cuts");
  const a = job.artifacts;
  const analyzeStep = job.steps.find((s) => s.step === "analyze");
  const proxyStep = job.steps.find((s) => s.step === "proxy");
  // Cuts come from the proxy step, silences from the analyze step: refetch when either changes.
  const version =
    a.scenes || a.silences
      ? [a.scenes, a.silences, proxyStep?.finished_at, analyzeStep?.finished_at].join("|")
      : null;
  const analysis = useAnalysis(job.id, version);

  const seek = (t: number) => {
    if (!video.current) return;
    video.current.currentTime = t;
    void video.current.play().catch(() => undefined);
  };

  return (
    <div className="media-panel">
      <div className="player">
        {a.proxy && proxyStep?.status === "SUCCEEDED" ? (
          <video
            ref={video}
            key={proxyStep.finished_at ?? "proxy"}
            src={`/media/jobs/${job.id}/proxy.mp4`}
            controls
            preload="metadata"
          />
        ) : (
          <div className="player-placeholder muted">프록시 영상을 만드는 중입니다</div>
        )}
      </div>
      <div className="signals">
        <div className="tabs">
          <button className={tab === "cuts" ? "active" : ""} onClick={() => setTab("cuts")}>
            장면 전환 {analysis.data?.scenes ? `(${analysis.data.scenes.length})` : ""}
          </button>
          <button className={tab === "silences" ? "active" : ""} onClick={() => setTab("silences")}>
            무음 {analysis.data?.silences ? `(${analysis.data.silences.length})` : ""}
          </button>
        </div>
        {!(tab === "cuts" ? analysis.data?.scenes : analysis.data?.silences) ? (
          <p className="muted small">분석이 끝나면 표시됩니다</p>
        ) : (
          <ol className="signal-list">
            {tab === "cuts" &&
              analysis.data?.scenes?.map((c) => (
                <li key={c.frame}>
                  <button className="link" onClick={() => seek(c.time)}>
                    {timecode(c.time, job.fps)}
                  </button>
                  <span className="muted small"> 점수 {c.score.toFixed(2)}</span>
                </li>
              ))}
            {tab === "silences" &&
              analysis.data?.silences?.map((s) => (
                <li key={s.start}>
                  <button className="link" onClick={() => seek(s.start)}>
                    {timecode(s.start, job.fps)}
                  </button>
                  <span className="muted small"> {(s.end - s.start).toFixed(2)}초</span>
                </li>
              ))}
          </ol>
        )}
      </div>
    </div>
  );
}
