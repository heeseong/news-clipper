import { useState } from "react";

import { useClip, useRegenerateMetadata } from "../api/newsroom";
import type { TextCandidate } from "../api/types";
import { timecode } from "../labels";

const SOURCE_LABEL: Record<string, string> = { script: "원고", title: "제목에서", llm: "LLM" };
const LLM_LABEL: Record<string, string> = { ok: "LLM 보완", failed: "LLM 실패", off: "LLM 없음" };

function Candidates({ items, current }: { items: TextCandidate[]; current: string }) {
  return (
    <ul className="candidates">
      {items.map((c, i) => (
        <li key={i} className={c.text === current ? "current" : ""}>
          <span className="tag">{SOURCE_LABEL[c.source] ?? c.source}</span>{" "}
          <span className="multiline">{c.text}</span>{" "}
          <span className="muted small">({c.text.length}자)</span>
          {c.warnings.map((w) => (
            <div key={w} className="small warn-text">
              {w}
            </div>
          ))}
        </li>
      ))}
    </ul>
  );
}

/** Metadata and thumbnail candidates of one clip (read-only; editing comes with M5). */
export function ClipMetaPanel({
  clipId,
  version,
  fps,
  seek,
}: {
  clipId: number;
  version: string;
  fps: number | null;
  seek: (t: number) => void;
}) {
  const [waitingFor, setWaitingFor] = useState<string | null | undefined>(undefined);
  const clip = useClip(clipId, version, waitingFor !== undefined);
  const regenerate = useRegenerateMetadata(clipId);
  const meta = clip.data?.metadata ?? null;
  if (waitingFor !== undefined && meta && meta.generated_at !== waitingFor) setWaitingFor(undefined);

  if (!clip.data) return <p className="muted small">불러오는 중…</p>;
  if (!meta && clip.data.thumbnails.length === 0)
    return <p className="muted small">메타데이터·썸네일은 구간이 정해진 클립에만 만듭니다</p>;

  return (
    <div className="clip-meta">
      {clip.data.thumbnail_url && (
        <img className="thumb-final" src={clip.data.thumbnail_url} alt="썸네일 (문구 포함)" />
      )}
      {clip.data.thumbnails.length > 0 && (
        <div className="thumb-grid">
          {clip.data.thumbnails.map((t) => (
            <button
              key={t.id}
              className={`thumb${t.selected ? " selected" : ""}`}
              title={`점수 ${t.score?.toFixed(2)} · 선명도 ${t.details.sharpness} · 노출 ${t.details.exposure} · 얼굴 ${t.details.face}${t.details.anchor ? " · 앵커 샷" : ""}`}
              onClick={() => t.frame_sec != null && seek(t.frame_sec)}
            >
              <img src={t.url} alt={`후보 ${t.rank}`} loading="lazy" />
              <span className="small">
                {t.rank}. {t.frame_sec != null ? timecode(t.frame_sec, fps) : ""}
              </span>
            </button>
          ))}
        </div>
      )}
      {meta && (
        <>
          <div className="small">
            <strong>제목</strong>
          </div>
          <Candidates items={meta.title_candidates} current={meta.title} />
          <div className="small">
            <strong>썸네일 문구</strong>
          </div>
          <Candidates items={meta.thumbnail_text_candidates} current={meta.thumbnail_text} />
          <div className="small">
            <strong>설명</strong>
          </div>
          <pre className="description small">{meta.description}</pre>
          {meta.summary && (
            <div className="small">
              <strong>요약 (LLM)</strong> {meta.summary}
            </div>
          )}
          <div className="small">
            <strong>태그</strong> {meta.tags.join(", ") || "없음"}
            {meta.hashtags.length > 0 && <> · {meta.hashtags.join(" ")}</>}
          </div>
          {meta.warnings.length > 0 && (
            <ul className="small warn-text">
              {meta.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          <div className="small muted">
            {LLM_LABEL[meta.llm_status] ?? meta.llm_status}
            {meta.llm_model && ` · ${meta.llm_model}`}
            {meta.llm_input_tokens + meta.llm_output_tokens > 0 &&
              ` · 토큰 ${meta.llm_input_tokens} / ${meta.llm_output_tokens}`}{" "}
            <button
              className="btn btn-small"
              disabled={regenerate.isPending || waitingFor !== undefined}
              onClick={() => {
                setWaitingFor(meta.generated_at);
                regenerate.mutate();
              }}
            >
              {waitingFor !== undefined ? "다시 만드는 중…" : "다시 만들기"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
