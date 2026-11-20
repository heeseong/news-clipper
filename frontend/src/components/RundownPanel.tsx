import { Fragment, useState } from "react";

import { isActive } from "../api/jobs";
import { useJobRundown, useRefreshRundown } from "../api/newsroom";
import type { JobDetail, RundownChange, RundownItem } from "../api/types";
import { CLIP_FLAG_LABEL, ITEM_TYPE_LABEL, SENTENCE_KIND_LABEL, formatDateTime, formatMinSec, timecode } from "../labels";
import { RundownPreview } from "./RundownPreview";

/** The job's cue sheet: waiting state with a manual re-check, or the parsed items. */
export function RundownPanel({ job }: { job: JobDetail }) {
  const step = job.steps.find((s) => s.step === "newsroom");
  const waiting = step?.status === "WAITING";
  const alignStep = job.steps.find((s) => s.step === "align");
  // Refetch when the newsroom step or the align step (which adds positions) finishes again.
  const version =
    step?.status === "SUCCEEDED" ? `${step.finished_at ?? "done"}|${alignStep?.finished_at ?? ""}` : null;
  const rundown = useJobRundown(job.id, version);
  const refresh = useRefreshRundown(job.id);
  const [pick, setPick] = useState<number | null>(job.cue_sheet_id);
  const busy = isActive(job) || refresh.isPending;

  if (!step || job.is_dummy) return null;

  return (
    <div className="rundown-panel">
      <div className="page-head">
        <h2>큐시트·원고</h2>
        {(waiting || version) && (
          <button
            className="btn btn-small"
            disabled={busy}
            title={busy ? "진행 중인 단계가 끝난 뒤 다시 조회할 수 있습니다" : undefined}
            onClick={() => refresh.mutate(pick)}
          >
            {waiting ? "큐시트 다시 조회" : "큐시트 다시 가져오기"}
          </button>
        )}
      </div>
      {refresh.isError && <p className="error-box">다시 조회하지 못했습니다: {refresh.error.message}</p>}

      {waiting && job.broadcast_date && (
        <RundownPreview
          programId={job.program_id}
          date={job.broadcast_date}
          value={pick}
          onChange={setPick}
          disabled={busy}
          missingHint="큐시트가 등록되면 '큐시트 다시 조회'를 누르세요. 다른 큐시트를 골라 진행할 수도 있습니다."
        />
      )}
      {!waiting && !version && <p className="muted small">큐시트 단계가 끝나면 표시됩니다</p>}
      {version && rundown.isPending && <p className="muted small">불러오는 중…</p>}
      {rundown.isError && <p className="error-box">큐시트를 불러오지 못했습니다: {rundown.error.message}</p>}

      {version && rundown.data && (
        <>
          <dl className="meta">
            <dt>큐시트</dt>
            <dd>
              {rundown.data.program_name} {rundown.data.broadcast_start_time?.slice(0, 5)} ·{" "}
              {rundown.data.edition}판 (#{rundown.data.newsroom_rundown_id}) · {rundown.data.match_reason}
            </dd>
            <dt>수정 시각</dt>
            <dd>
              {rundown.data.confirmed_at?.replace("T", " ").slice(0, 16) ?? "—"} · 가져온 시각{" "}
              {formatDateTime(rundown.data.fetched_at)}
            </dd>
            <dt>아이템</dt>
            <dd>
              {rundown.data.counts.items}개 · 리포트 {rundown.data.counts.reports}개 · 업로드 대상{" "}
              <strong>{rundown.data.counts.uploads}</strong>개 · 예정 총 길이{" "}
              {formatMinSec(rundown.data.counts.planned_total_seconds)}
            </dd>
          </dl>
          {rundown.data.warnings.length > 0 && (
            <ul className="warn-box">
              {rundown.data.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          {rundown.data.changes.length > 0 && <Changes changes={rundown.data.changes} />}
          <ItemTable items={rundown.data.items} fps={job.fps} />
        </>
      )}
    </div>
  );
}

function Changes({ changes }: { changes: RundownChange[] }) {
  return (
    <ul className="warn-box small">
      {changes.map((c) => {
        const parts = [
          c.cue_sheet && `큐시트 변경 #${c.cue_sheet.from}→#${c.cue_sheet.to}`,
          c.added.length > 0 && `추가 ${c.added.map((i) => `${i.item_no}.${i.title}`).join(", ")}`,
          c.removed.length > 0 && `삭제 ${c.removed.map((i) => i.title).join(", ")}`,
          c.moved.length > 0 && `순서 변경 ${c.moved.map((i) => `${i.title}(${i.from}→${i.to})`).join(", ")}`,
          c.edited.length > 0 && `수정 ${c.edited.map((i) => i.title).join(", ")}`,
        ].filter(Boolean);
        return (
          <li key={c.at}>
            {formatDateTime(c.at)} 판 변경 {c.from}→{c.to}: {parts.join(" · ") || "아이템 변화 없음"}
          </li>
        );
      })}
    </ul>
  );
}

function ItemTable({ items, fps }: { items: RundownItem[]; fps: number | null }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <table className="table rundown-table">
      <thead>
        <tr>
          <th>No</th>
          <th>형식</th>
          <th>제목</th>
          <th>기자</th>
          <th>출처</th>
          <th>예정</th>
          <th>추정</th>
          <th>업로드</th>
          <th>녹화본 위치</th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => (
          <Fragment key={it.id}>
            <tr className={it.upload ? "row-upload" : "row-muted"}>
              <td>{it.item_no}</td>
              <td title={it.type_reason ?? undefined}>{ITEM_TYPE_LABEL[it.type] ?? it.type}</td>
              <td>
                <button className="link" onClick={() => setOpen(open === it.id ? null : it.id)}>
                  {it.series && <span className="tag">{it.series}</span>}
                  {it.title}
                </button>
                {it.parse_warnings.length > 0 && (
                  <span className="tag tag-warn" title={it.parse_warnings.join("\n")}>
                    경고 {it.parse_warnings.length}
                  </span>
                )}
              </td>
              <td title={it.cue_sheet_reporter ? `큐시트 기자: ${it.cue_sheet_reporter} (사인오프 기자로 표시)` : undefined}>
                {it.reporter ?? "—"}
                {it.cue_sheet_reporter && <span className="muted small"> ({it.cue_sheet_reporter})</span>}
              </td>
              <td title={it.source_reason ?? undefined}>{it.source ?? "자사"}</td>
              <td>{formatMinSec(it.planned_duration)}</td>
              <td>{formatMinSec(it.estimated_duration)}</td>
              <td>{it.upload ? "대상" : "—"}</td>
              <td className="small">
                {it.align_status === "ALIGNED" && it.aligned_start != null ? (
                  <span title={`문장 점수 중앙값 ${it.align_score?.toFixed(2)}`}>{timecode(it.aligned_start, fps)}</span>
                ) : it.align_status === "NOT_FOUND" ? (
                  <span className="warn-text">찾지 못함</span>
                ) : (
                  "—"
                )}
                {it.align_flags.map((f) => (
                  <span key={f} className="tag tag-warn">
                    {CLIP_FLAG_LABEL[f] ?? f}
                  </span>
                ))}
              </td>
            </tr>
            {open === it.id && (
              <tr>
                <td colSpan={9}>
                  <Script item={it} />
                </td>
              </tr>
            )}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

function Script({ item }: { item: RundownItem }) {
  const script = item.parsed_script;
  if (!script) return <p className="muted small">원고 없음</p>;
  return (
    <div className="script small">
      {item.title_raw && item.title_raw !== item.title && <p className="muted">원고 제목: {item.title_raw}</p>}
      <p className="muted">
        형식 판별: {item.type_reason}
        {item.source_reason && ` · 출처: ${item.source_reason}`}
      </p>
      {item.parse_warnings.length > 0 && <p className="error-text">경고: {item.parse_warnings.join(", ")}</p>}
      <ol>
        {script.sentences.map((s, i) => (
          <li key={i} className={`sentence sentence-${s.section}`}>
            <span className="tag">
              {s.section === "lead" ? "리드" : "리포트"} · {SENTENCE_KIND_LABEL[s.kind] ?? s.kind}
            </span>
            {s.speaker && <span className="muted"> {s.speaker}: </span>}
            {s.text}
          </li>
        ))}
      </ol>
    </div>
  );
}
