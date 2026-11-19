import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useCreateDummyJob, useJobs, usePipeline } from "../api/jobs";
import { JobStatusBadge } from "../components/StatusBadge";
import { STEP_LABEL, STAGE_LABEL, formatDateTime } from "../labels";

export function JobListPage() {
  const jobs = useJobs();

  return (
    <section>
      <div className="page-head">
        <h1>작업 목록</h1>
        <DummyJobPanel />
      </div>

      {jobs.isPending && <p className="muted">불러오는 중…</p>}
      {jobs.isError && <p className="error-box">작업 목록을 불러오지 못했습니다: {jobs.error.message}</p>}
      {jobs.data && jobs.data.items.length === 0 && (
        <div className="empty">
          <p>작업이 없습니다.</p>
          <p className="muted">녹화본을 업로드하면 여기에 표시됩니다.</p>
        </div>
      )}
      {jobs.data && jobs.data.items.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>프로그램</th>
              <th>방송일</th>
              <th>상태</th>
              <th>현재 단계</th>
              <th>생성</th>
            </tr>
          </thead>
          <tbody>
            {jobs.data.items.map((job) => (
              <tr key={job.id}>
                <td>
                  <Link to={`/jobs/${job.id}`}>{job.id}</Link>
                </td>
                <td>
                  {job.program_name ?? "—"}
                  {job.is_dummy && <span className="tag">더미</span>}
                </td>
                <td>{job.broadcast_date ?? "—"}</td>
                <td>
                  <JobStatusBadge status={job.status} />
                </td>
                <td>
                  {job.current_step
                    ? (STAGE_LABEL[job.current_step] ?? STEP_LABEL[job.current_step] ?? job.current_step)
                    : "—"}
                </td>
                <td className="muted">{formatDateTime(job.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** Development helper: start a job whose steps only sleep (POST /api/jobs/dummy). */
function DummyJobPanel() {
  const pipeline = usePipeline();
  const create = useCreateDummyJob();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [seconds, setSeconds] = useState(2);
  const [failStep, setFailStep] = useState("");
  const [flakyStep, setFlakyStep] = useState("");
  const steps = pipeline.data?.flatMap((s) => s.steps) ?? [];

  const submit = () =>
    create.mutate(
      {
        seconds,
        fail_steps: failStep ? [failStep] : [],
        flaky_steps: flakyStep ? [flakyStep] : [],
      },
      { onSuccess: (job) => navigate(`/jobs/${job.id}`) },
    );

  if (!open) {
    return (
      <button className="btn" onClick={() => setOpen(true)}>
        더미 작업 실행…
      </button>
    );
  }
  return (
    <div className="dummy-panel">
      <label>
        단계당 대기(초)
        <input
          type="number"
          min={0}
          max={60}
          step={0.5}
          value={seconds}
          onChange={(e) => setSeconds(Number(e.target.value))}
        />
      </label>
      <label>
        실패시킬 단계
        <StepSelect steps={steps} value={failStep} onChange={setFailStep} />
      </label>
      <label>
        일시 오류(자동 재시도)
        <StepSelect steps={steps} value={flakyStep} onChange={setFlakyStep} />
      </label>
      <button className="btn btn-primary" onClick={submit} disabled={create.isPending}>
        실행
      </button>
      <button className="btn" onClick={() => setOpen(false)}>
        닫기
      </button>
      {create.isError && <span className="error-text">{create.error.message}</span>}
    </div>
  );
}

function StepSelect(props: { steps: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <select value={props.value} onChange={(e) => props.onChange(e.target.value)}>
      <option value="">없음</option>
      {props.steps.map((s) => (
        <option key={s} value={s}>
          {STEP_LABEL[s] ?? s}
        </option>
      ))}
    </select>
  );
}
