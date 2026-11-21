import { useState } from "react";
import { Link } from "react-router-dom";

import { usePublishSummary, usePublishTasks, useTaskAction } from "../api/publish";
import type { PublishTask } from "../api/types";
import { PRIVACY_LABEL, PUBLISH_STATUS_LABEL, formatBytes, formatDateTime } from "../labels";

const FILTERS: [string, string][] = [
  ["", "전체"],
  ["QUEUED,UPLOADING", "대기·업로드 중"],
  ["SCHEDULED", "예약됨"],
  ["PUBLISHED", "게시됨"],
  ["FAILED", "실패"],
  ["CANCELLED", "취소"],
];

/** Upload dashboard (spec 5.10, plan 7.5): per channel today, API quota, every upload. */
export function PublishPage() {
  const [status, setStatus] = useState("");
  const summary = usePublishSummary();
  const tasks = usePublishTasks({ status: status || undefined });

  return (
    <div className="publish-page">
      <h1>게시 현황</h1>
      <div className="summary-cards">
        {summary.data?.channels.map((c) => (
          <div key={c.channel.id} className="summary-card">
            <strong>{c.channel.name}</strong>
            {c.channel.status !== "active" && <span className="tag tag-warn"> 다시 연결 필요</span>}
            <div className="small">
              오늘 예약 {c.scheduled_today} · 오늘 게시 {c.published_today} · 대기 {c.queued + c.uploading}
              {c.failed > 0 && <span className="error-text"> · 실패 {c.failed}</span>}
            </div>
          </div>
        ))}
        {summary.data?.quota.map((q) => (
          <div key={q.platform} className="summary-card">
            <strong>YouTube API 할당량</strong>
            <meter min={0} max={q.limit} value={q.used} high={q.limit * 0.8} />
            <div className="small">
              {q.used.toLocaleString()} / {q.limit.toLocaleString()} units · 남은 업로드 약 {q.uploads_left}건 · 초기화{" "}
              {formatDateTime(q.resets_at)}
            </div>
          </div>
        ))}
        {summary.data && summary.data.channels.length === 0 && (
          <p className="muted">
            연결된 채널이 없습니다. <Link to="/channels">채널 연결</Link>
          </p>
        )}
      </div>
      <div className="row small">
        {FILTERS.map(([v, label]) => (
          <button key={v} className={`btn btn-small${status === v ? " btn-primary" : ""}`} onClick={() => setStatus(v)}>
            {label}
          </button>
        ))}
      </div>
      {tasks.data?.length === 0 ? (
        <p className="muted">업로드가 없습니다.</p>
      ) : (
        <table className="table publish-table">
          <thead>
            <tr>
              <th>기사</th>
              <th>채널</th>
              <th>상태</th>
              <th>공개</th>
              <th>예약(KST)</th>
              <th>영상</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {tasks.data?.map((t) => (
              <TaskRow key={t.id} task={t} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function useTaskRun(t: PublishTask) {
  const action = useTaskAction();
  const run = (a: "retry" | "cancel" | "check") => action.mutate({ id: t.id, action: a }, { onError: (e) => window.alert((e as Error).message) });
  return { run, busy: action.isPending };
}

function TaskStatus({ task: t }: { task: PublishTask }) {
  const progress = t.status === "UPLOADING" && t.file_size ? Math.round((100 * t.bytes_sent) / t.file_size) : null;
  return (
    <>
      <span className={`tag publish-${t.status}`}>{PUBLISH_STATUS_LABEL[t.status] ?? t.status}</span>
      {progress != null && <span className="small"> {progress}% ({formatBytes(t.bytes_sent)} / {formatBytes(t.file_size)})</span>}
      {t.status === "QUEUED" && t.next_retry_at && <div className="small muted">다음 시도 {formatDateTime(t.next_retry_at)}</div>}
      {t.last_error && <div className="small error-text">{t.last_error}</div>}
      {t.thumbnail_status === "failed" && (
        <div className="small warn-text">
          썸네일 실패: {t.thumbnail_error}
          {t.thumbnail_next_at && <> (다시 시도 {formatDateTime(t.thumbnail_next_at)})</>}
        </div>
      )}
      <SyncStatus task={t} />
    </>
  );
}

/** Edits after the upload (M8): pushed to YouTube automatically. */
function SyncStatus({ task: t }: { task: PublishTask }) {
  if (!t.sync_status) return null;
  if (t.sync_status === "ok")
    return <div className="small muted">수정 사항 YouTube 반영됨 {t.synced_at && formatDateTime(t.synced_at)}</div>;
  if (t.sync_status === "failed") return <div className="small error-text">수정 사항 반영 실패: {t.sync_error}</div>;
  return (
    <div className="small muted">
      수정 사항을 YouTube에 반영하는 중…{t.sync_error && <span className="warn-text"> ({t.sync_error}, 다시 시도 예정)</span>}
    </div>
  );
}

function TaskActions({ task: t }: { task: PublishTask }) {
  const { run, busy } = useTaskRun(t);
  return (
    <span className="small nowrap">
      {(t.status === "FAILED" || (t.status === "QUEUED" && t.next_retry_at) || t.thumbnail_status === "failed" || t.sync_status === "failed") && (
        <button className="link" disabled={busy} onClick={() => run("retry")}>
          {t.status === "FAILED" || t.status === "QUEUED" ? "다시 시도" : t.sync_status === "failed" ? "다시 반영" : "썸네일 다시"}
        </button>
      )}{" "}
      {t.remote_id && !["CANCELLED", "UPLOADING"].includes(t.status) && (
        <button className="link" disabled={busy} onClick={() => run("check")}>
          상태 확인
        </button>
      )}{" "}
      {!["CANCELLED", "UPLOADING"].includes(t.status) && (
        <button
          className="link danger"
          disabled={busy}
          onClick={() => {
            const msg = t.remote_id
              ? `YouTube에 올라간 영상(${t.remote_id})을 삭제하고 업로드를 취소할까요? 되돌릴 수 없습니다.`
              : "업로드를 취소할까요?";
            if (window.confirm(msg)) run("cancel");
          }}
        >
          {t.remote_id ? "삭제·취소" : "취소"}
        </button>
      )}
    </span>
  );
}

function TaskRow({ task: t }: { task: PublishTask }) {
  return (
    <tr>
      <td>
        {t.job_id ? (
          <Link to={`/jobs/${t.job_id}/clips/${t.clip_id}`}>
            {t.item_no}. {t.title}
          </Link>
        ) : (
          `클립 ${t.clip_id}`
        )}
      </td>
      <td>{t.channel_name}</td>
      <td>
        <TaskStatus task={t} />
      </td>
      <td className="small">{PRIVACY_LABEL[t.privacy] ?? t.privacy}</td>
      <td className="small">{t.publish_at ? formatDateTime(t.publish_at) : "—"}</td>
      <td className="small">
        {t.remote_url ? (
          <a href={t.remote_url} target="_blank" rel="noreferrer">
            {t.remote_id}
          </a>
        ) : (
          "—"
        )}
      </td>
      <td>
        <TaskActions task={t} />
      </td>
    </tr>
  );
}

/** One upload in the clip editor (narrow column). */
export function TaskCard({ task: t }: { task: PublishTask }) {
  return (
    <div className="task-card">
      <div>
        <strong>{t.channel_name}</strong> <TaskStatus task={t} />
      </div>
      <div className="small">
        {PRIVACY_LABEL[t.privacy] ?? t.privacy}
        {t.publish_at && <> · 예약 {formatDateTime(t.publish_at)}</>}
        {t.remote_url && (
          <>
            {" · "}
            <a href={t.remote_url} target="_blank" rel="noreferrer">
              YouTube에서 보기
            </a>
          </>
        )}
      </div>
      <TaskActions task={t} />
    </div>
  );
}
