import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useAnalysis, useJob, usePipeline, useRetryStep } from "../api/jobs";
import { useJobClips, useJobRundown } from "../api/newsroom";
import { useBulk } from "../api/review";
import type { Clip, JobDetail, JobStep } from "../api/types";
import { ClipMetaPanel } from "../components/ClipMetaPanel";
import { JobTimeline } from "../components/JobTimeline";
import { RundownPanel } from "../components/RundownPanel";
import { JobStatusBadge, StepStatusBadge } from "../components/StatusBadge";
import {
  CLIP_FLAG_LABEL,
  CLIP_STATUS_LABEL,
  SNAP_LABEL,
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
      {data.error && (
        <p className={data.status === "WAITING_RUNDOWN" ? "warn-box" : "error-box"}>{data.error}</p>
      )}

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
      {!data.is_dummy && <RundownPanel job={data} />}
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
        <div
          className={`small ${step.status === "SKIPPED" ? "muted" : step.status === "WAITING" ? "warn-text" : "error-text"}`}
        >
          {step.error}
        </div>
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
  const [tab, setTab] = useState<"clips" | "cuts" | "silences">("clips");
  const a = job.artifacts;
  const analyzeStep = job.steps.find((s) => s.step === "analyze");
  const proxyStep = job.steps.find((s) => s.step === "proxy");
  // Cuts come from the proxy step, silences from the analyze step: refetch when either changes.
  const version =
    a.scenes || a.silences
      ? [a.scenes, a.silences, proxyStep?.finished_at, analyzeStep?.finished_at].join("|")
      : null;
  const analysis = useAnalysis(job.id, version);
  const alignStep = job.steps.find((s) => s.step === "align");
  // Clips come from align; titles and thumbnails from the metadata and thumbnails steps.
  const clipSteps = job.steps.filter((s) => ["align", "metadata", "thumbnails"].includes(s.step));
  const clipsVersion =
    alignStep?.status === "SUCCEEDED" ? clipSteps.map((s) => `${s.status}:${s.finished_at}`).join("|") : null;
  const clips = useJobClips(job.id, clipsVersion);
  const [openClip, setOpenClip] = useState<number | null>(null);
  const newsroomStep = job.steps.find((s) => s.step === "newsroom");
  const rundown = useJobRundown(
    job.id,
    newsroomStep?.status === "SUCCEEDED" ? `${newsroomStep.finished_at ?? "done"}|${alignStep?.finished_at ?? ""}` : null,
  );
  const navigate = useNavigate();
  const [t, setT] = useState(0);
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const on = () => setT(v.currentTime);
    v.addEventListener("timeupdate", on);
    v.addEventListener("seeked", on);
    return () => {
      v.removeEventListener("timeupdate", on);
      v.removeEventListener("seeked", on);
    };
  });

  const seek = (t: number) => {
    if (!video.current) return;
    video.current.currentTime = t;
    void video.current.play().catch(() => undefined);
  };

  return (
    <>
    {job.duration && rundown.data && (
      <JobTimeline
        jobId={job.id}
        duration={job.duration}
        fps={job.fps}
        items={rundown.data.items}
        clips={clips.data ?? []}
        t={t}
        onSeek={(time) => seek(time)}
        onOpen={(clipId) => navigate(`/jobs/${job.id}/clips/${clipId}`)}
      />
    )}
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
          <button className={tab === "clips" ? "active" : ""} onClick={() => setTab("clips")}>
            클립 {clips.data ? `(${clips.data.length})` : ""}
          </button>
          <button className={tab === "cuts" ? "active" : ""} onClick={() => setTab("cuts")}>
            장면 전환 {analysis.data?.scenes ? `(${analysis.data.scenes.length})` : ""}
          </button>
          <button className={tab === "silences" ? "active" : ""} onClick={() => setTab("silences")}>
            무음 {analysis.data?.silences ? `(${analysis.data.silences.length})` : ""}
          </button>
        </div>
        {tab === "clips" ? (
          !clips.data ? (
            <p className="muted small">정렬이 끝나면 표시됩니다</p>
          ) : clips.data.length === 0 ? (
            <p className="muted small">업로드 대상 기사가 없습니다</p>
          ) : (
            <>
            <BulkBar clips={clips.data} />
            <ol className="signal-list clip-list">
              {clips.data.map((c) => (
                <li key={c.id}>
                  <div className="clip-head">
                    {c.thumbnail_url && <img className="clip-thumb" src={c.thumbnail_url} alt="" />}
                    <div>
                      <strong>
                        <Link to={`/jobs/${job.id}/clips/${c.id}`}>
                          {c.item_no}. {c.title}
                        </Link>
                      </strong>{" "}
                      <span className={`tag status-${c.status}`}>{CLIP_STATUS_LABEL[c.status] ?? c.status}</span>
                      {c.youtube_title && c.youtube_title !== c.title && (
                        <div className="small">{c.youtube_title}</div>
                      )}
                      {(c.youtube_title || c.thumbnail_url) && (
                        <div>
                          <button className="link small" onClick={() => setOpenClip(openClip === c.id ? null : c.id)}>
                            {openClip === c.id ? "메타데이터 닫기" : "메타데이터·썸네일 보기"}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                  {c.start_sec != null && c.end_sec != null ? (
                    <div className="small">
                      <button className="link" onClick={() => seek(c.start_sec!)}>
                        {timecode(c.start_sec, job.fps)}
                      </button>
                      {" – "}
                      <button className="link" onClick={() => seek(Math.max(c.start_sec!, c.end_sec! - 3))}>
                        {timecode(c.end_sec, job.fps)}
                      </button>
                      <span className="muted">
                        {" "}
                        {formatDuration(c.end_sec - c.start_sec)} · {SNAP_LABEL[c.start_snap ?? ""] ?? c.start_snap} →{" "}
                        {SNAP_LABEL[c.end_snap ?? ""] ?? c.end_snap}
                      </span>
                    </div>
                  ) : (
                    <div className="small warn-text">
                      구간 직접 지정 필요
                      {c.suggested_start != null && (
                        <>
                          {" "}
                          · 정렬 제안{" "}
                          <button className="link" onClick={() => seek(c.suggested_start!)}>
                            {timecode(c.suggested_start, job.fps)}
                          </button>
                        </>
                      )}
                    </div>
                  )}
                  {c.flags.length > 0 && (
                    <div>
                      {c.flags.map((f) => (
                        <span key={f} className="tag tag-warn">
                          {CLIP_FLAG_LABEL[f] ?? f}
                        </span>
                      ))}
                    </div>
                  )}
                  {openClip === c.id && clipsVersion && (
                    <ClipMetaPanel clipId={c.id} version={clipsVersion} fps={job.fps} seek={seek} />
                  )}
                </li>
              ))}
            </ol>
            </>
          )
        ) : !(tab === "cuts" ? analysis.data?.scenes : analysis.data?.silences) ? (
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
    </>
  );
}

/** Approve several clips, or schedule them one after another (spec 5.8 일괄 처리). */
function BulkBar({ clips }: { clips: Clip[] }) {
  const bulk = useBulk();
  const reviewable = clips.filter((c) => c.status === "DRAFT" && !c.needs_manual_range);
  const [first, setFirst] = useState("");
  const [interval, setInterval_] = useState(10);
  const [result, setResult] = useState<string | null>(null);
  const ids = clips.filter((c) => ["DRAFT", "APPROVED", "RENDERING", "RENDERED"].includes(c.status)).map((c) => c.id);
  if (!clips.length) return null;
  const report = (rows: { ok: boolean; clip_id: number; problems: string[]; error: string | null }[]) => {
    const failed = rows.filter((r) => !r.ok);
    setResult(
      failed.length
        ? `${rows.length - failed.length}건 처리, ${failed.length}건 실패: ` +
            failed.map((r) => `#${clips.find((c) => c.id === r.clip_id)?.item_no} ${r.problems.join("·") || r.error}`).join(" / ")
        : `${rows.length}건 처리했습니다`,
    );
  };
  return (
    <div className="bulk-bar small">
      <button
        className="btn btn-small"
        disabled={!reviewable.length || bulk.isPending}
        onClick={() => bulk.mutateAsync({ clip_ids: reviewable.map((c) => c.id), action: "approve" }).then(report, (e: Error) => setResult(e.message))}
      >
        검수 전 {reviewable.length}건 일괄 승인
      </button>
      <span>
        순차 예약 <input type="datetime-local" value={first} onChange={(e) => setFirst(e.target.value)} /> 부터{" "}
        <input type="number" min={1} value={interval} onChange={(e) => setInterval_(Number(e.target.value))} style={{ width: "4em" }} />분 간격{" "}
        <button
          className="btn btn-small"
          disabled={!first || !ids.length || bulk.isPending}
          onClick={() =>
            bulk
              .mutateAsync({ clip_ids: ids, action: "schedule", first_at: `${first}:00+09:00`, interval_minutes: interval, privacy: "public" })
              .then(report, (e: Error) => setResult(e.message))
          }
        >
          {ids.length}건 예약
        </button>
      </span>
      {result && <div>{result}</div>}
    </div>
  );
}
