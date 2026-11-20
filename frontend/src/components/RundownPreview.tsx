import { useRundownPreview } from "../api/newsroom";
import type { RundownSummary } from "../api/types";
import { formatMinSec } from "../labels";

function rundownLabel(r: RundownSummary): string {
  const start = r.broadcast_start_time ? ` ${r.broadcast_start_time.slice(0, 5)}` : "";
  return `${r.program_name}${start} · ${r.version}판 (#${r.cue_sheet_id})`;
}

/**
 * Shows the cue sheet a program + date resolves to, and lets the user pick another one
 * (`value` = picked cueSheetId, null = automatic match by program name and time).
 */
export function RundownPreview(props: {
  programId: number | null;
  date: string;
  value: number | null;
  onChange: (cueSheetId: number | null) => void;
  disabled?: boolean;
  /** Text for the "no cue sheet" case. */
  missingHint: string;
}) {
  const preview = useRundownPreview(props.programId, props.date, props.value);

  if (!props.date) return null;
  if (preview.isPending) return <div className="rundown-preview muted small">큐시트를 확인하는 중…</div>;
  if (preview.isError)
    return (
      <div className="rundown-preview warn-box">
        큐시트를 조회하지 못했습니다: {preview.error.message}
        <div className="small">{props.missingHint}</div>
      </div>
    );

  const p = preview.data;
  const c = p.counts;
  return (
    <div className={`rundown-preview ${p.matched ? "ok-box" : "warn-box"}`}>
      {p.matched ? (
        <>
          <div>
            <strong>큐시트</strong> {rundownLabel(p.matched)}
            {props.value != null && <span className="tag">직접 선택</span>}
          </div>
          {c && (
            <div className="small">
              아이템 {c.items}개 · 리포트 {c.reports}개 (업로드 대상 <strong>{c.uploads}</strong>개
              {c.excluded > 0 && `, 타사 제외 ${c.excluded}개`}) · 예정 총 길이{" "}
              {formatMinSec(c.planned_total_seconds)}
            </div>
          )}
        </>
      ) : (
        <>
          <div>
            <strong>큐시트 없음</strong> — {p.reason}
          </div>
          <div className="small">{props.missingHint}</div>
        </>
      )}
      {p.warnings.length > 0 && (
        <ul className="small">
          {p.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {(p.day_rundowns.length > 0 || props.value != null) && (
        <label className="small inline-form">
          {p.ambiguous ? "큐시트를 골라 주세요" : "다른 큐시트"}
          <select
            value={props.value ?? ""}
            disabled={props.disabled}
            onChange={(e) => props.onChange(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">자동 (프로그램명·방송 시각)</option>
            {p.day_rundowns.map((r) => (
              <option key={r.cue_sheet_id} value={r.cue_sheet_id}>
                {rundownLabel(r)}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}
