import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAnalysis, useJob } from "../api/jobs";
import {
  useAudit,
  useClipAction,
  useClipDetail,
  useClipList,
  useClipLock,
  usePatchClip,
  useSentences,
  useThumbnailActions,
  useWaveform,
} from "../api/review";
import { useChannels, usePublishClip } from "../api/publish";
import type { AlignedSentence, Channel, Clip, ClipDetail, ClipMetadata, ClipPatch } from "../api/types";
import { BoundaryStrip } from "../components/BoundaryStrip";
import { CLIP_FLAG_LABEL, CLIP_STATUS_LABEL, SENTENCE_KIND_LABEL, SNAP_LABEL, formatDuration, timecode } from "../labels";
import { useReviewer } from "../reviewer";
import { TaskCard } from "./PublishPage";

const LOW_SCORE = -5; // alignment score below which a sentence is shown as doubtful (PoC threshold)
const LOOP_SECONDS = 3;
const TITLE_MAX = 100;
const EDITABLE = ["DRAFT", "NOT_FOUND"];
const REOPENABLE = ["APPROVED", "RENDERING", "RENDERED", "REJECTED", "UPLOAD_FAILED"];
// Publishing settings (channels, visibility, schedule) can change until the upload starts.
const PUBLISH_EDITABLE = [...EDITABLE, "APPROVED", "RENDERING", "RENDERED", "UPLOAD_FAILED"];

const KEYS: [string, string][] = [
  ["Space", "재생/정지"],
  ["← →", "1프레임"],
  ["Shift+← →", "1초"],
  ["I / O", "현재 위치를 시작/끝으로"],
  ["[ / ]", "시작/끝으로 이동"],
  ["L", "가까운 경계 ±3초 반복"],
  ["Ctrl+S", "메타데이터 저장"],
  ["Ctrl+Enter", "승인"],
  ["N / P", "다음/이전 클립"],
];

function isTyping(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

/** "2026-11-21T09:00" in KST <-> ISO */
function toKstInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(new Date(iso).getTime() + 9 * 3600_000);
  return d.toISOString().slice(0, 16);
}
function fromKstInput(v: string): string | null {
  return v ? `${v}:00+09:00` : null;
}

export function ClipEditorPage() {
  const jobId = Number(useParams().id);
  const clipId = Number(useParams().clipId);
  const reviewer = useReviewer();
  const job = useJob(jobId);
  const clip = useClipDetail(clipId);
  const list = useClipList(jobId);

  if (!reviewer) return <p className="error-box">위쪽 막대에 검수자 이름을 먼저 입력해 주세요.</p>;
  if (clip.isError) return <p className="error-box">클립을 불러오지 못했습니다: {clip.error.message}</p>;
  if (!job.data || !clip.data) return <p className="muted">불러오는 중…</p>;
  return <Editor key={clipId} jobId={jobId} fps={job.data.fps ?? 29.97} duration={job.data.duration ?? 0} clip={clip.data} clips={list.data ?? []} />;
}

function Editor({ jobId, fps, duration, clip, clips }: { jobId: number; fps: number; duration: number; clip: ClipDetail; clips: Clip[] }) {
  const navigate = useNavigate();
  const video = useRef<HTMLVideoElement>(null);
  const [t, setT] = useState(clip.start_sec ?? clip.suggested_start ?? 0);
  const [loop, setLoop] = useState<[number, number] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const editable = EDITABLE.includes(clip.status);
  const holder = useClipLock(clip.id, editable);
  const readOnly = !editable || !!holder;

  const analysis = useAnalysis(jobId, "editor");
  const waveform = useWaveform(jobId);
  const sentences = useSentences(clip.id, `${clip.start_sec}-${clip.end_sec}`);
  const patch = usePatchClip(clip.id);
  const action = useClipAction(clip.id);

  const index = clips.findIndex((c) => c.id === clip.id);
  const go = useCallback((offset: number) => {
    const next = clips[index + offset];
    if (next) navigate(`/jobs/${jobId}/clips/${next.id}`);
  }, [clips, index, jobId, navigate]);
  const nextToReview = useCallback(() => {
    const rest = [...clips.slice(index + 1), ...clips.slice(0, index)];
    const next = rest.find((c) => EDITABLE.includes(c.status));
    if (next) navigate(`/jobs/${jobId}/clips/${next.id}`);
  }, [clips, index, jobId, navigate]);

  const seek = useCallback((time: number) => {
    const v = video.current;
    if (!v) return;
    v.currentTime = Math.max(0, Math.min(duration || v.duration || time, time));
    setT(v.currentTime);
  }, [duration]);

  const run = useCallback(async (p: Promise<unknown>) => {
    setError(null);
    try {
      await p;
    } catch (e) {
      setError(e instanceof ApiError && e.problems.length ? `${e.message}` : (e as Error).message);
    }
  }, []);

  const setRange = useCallback((a: number, b: number) => {
    if (readOnly) return;
    void run(patch.mutateAsync({ start_sec: Math.round(a * 1000) / 1000, end_sec: Math.round(b * 1000) / 1000 }));
  }, [patch, readOnly, run]);

  const approve = useCallback(async () => {
    setError(null);
    try {
      await action.mutateAsync({ action: "approve" });
      nextToReview();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [action, nextToReview]);

  const inT = clip.start_sec;
  const outT = clip.end_sec;

  // Loop playback around a boundary.
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const onTime = () => {
      setT(v.currentTime);
      if (loop && v.currentTime >= loop[1]) v.currentTime = loop[0];
    };
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("seeked", onTime);
    return () => {
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("seeked", onTime);
    };
  }, [loop]);

  // Keyboard.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        if (!readOnly) void approve();
        return;
      }
      if (isTyping(e) || e.ctrlKey || e.metaKey || e.altKey) return;
      const v = video.current;
      if (!v) return;
      const frame = 1 / fps;
      switch (e.key) {
        case " ":
          e.preventDefault();
          if (v.paused) void v.play();
          else v.pause();
          break;
        case "ArrowLeft":
        case "ArrowRight": {
          e.preventDefault();
          v.pause();
          const step = e.shiftKey ? 1 : frame;
          seek(v.currentTime + (e.key === "ArrowLeft" ? -step : step));
          break;
        }
        case "i":
        case "I":
          if (outT != null && v.currentTime < outT - 1) setRange(v.currentTime, outT);
          else if (outT == null) setRange(v.currentTime, Math.min(duration, v.currentTime + 30));
          break;
        case "o":
        case "O":
          if (inT != null && v.currentTime > inT + 1) setRange(inT, v.currentTime);
          break;
        case "[":
          if (inT != null) seek(inT);
          break;
        case "]":
          if (outT != null) seek(outT - frame);
          break;
        case "l":
        case "L": {
          if (loop) {
            setLoop(null);
            break;
          }
          const b = inT != null && outT != null ? (Math.abs(v.currentTime - inT) < Math.abs(v.currentTime - outT) ? inT : outT) : v.currentTime;
          setLoop([b - LOOP_SECONDS, b + LOOP_SECONDS]);
          seek(b - LOOP_SECONDS);
          void v.play();
          break;
        }
        case "n":
        case "N":
          go(1);
          break;
        case "p":
        case "P":
          go(-1);
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [approve, duration, fps, go, inT, loop, outT, readOnly, seek, setRange]);

  // Start at the clip.
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const start = () => seek(clip.start_sec ?? clip.suggested_start ?? 0);
    if (v.readyState >= 1) start();
    else v.addEventListener("loadedmetadata", start, { once: true });
  }, [clip.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const center = inT ?? clip.suggested_start ?? t;
  const end = outT ?? clip.suggested_end ?? center + 30;
  const view: [number, number] = [Math.max(0, center - 8), Math.min(duration || end + 8, end + 8)];
  const warnings = [...clip.flags.map((f) => CLIP_FLAG_LABEL[f] ?? f), ...(clip.metadata?.warnings ?? [])];

  return (
    <div className="editor">
      <div className="editor-head">
        <Link to={`/jobs/${jobId}`}>← 작업으로</Link>
        <h1>
          {clip.item_no}. {clip.title}
        </h1>
        <span className={`tag status-${clip.status}`}>{CLIP_STATUS_LABEL[clip.status] ?? clip.status}</span>
        <span className="muted small">
          {index + 1} / {clips.length}{" "}
          <button className="link" onClick={() => go(-1)} disabled={index <= 0}>이전(P)</button>{" "}
          <button className="link" onClick={() => go(1)} disabled={index >= clips.length - 1}>다음(N)</button>
        </span>
      </div>
      {holder && <p className="error-box">{holder} — 읽기 전용으로 엽니다.</p>}
      {error && <p className="error-box" role="alert">{error}</p>}

      <div className="editor-grid">
        <section className="editor-left">
          <video ref={video} className="editor-video" src={`/media/jobs/${jobId}/proxy.mp4`} preload="auto" controls={false} onClick={() => { const v = video.current; if (v) { if (v.paused) void v.play(); else v.pause(); } }} />
          <div className="editor-transport small">
            <strong>{timecode(t, fps)}</strong>
            <span>
              IN {inT != null ? timecode(inT, fps) : "—"} · OUT {outT != null ? timecode(outT, fps) : "—"}
              {inT != null && outT != null && <> · {formatDuration(outT - inT)}</>}
              {clip.planned_duration != null && <span className="muted"> (예정 {formatDuration(clip.planned_duration)})</span>}
            </span>
            <span className="muted">
              {SNAP_LABEL[clip.start_snap ?? ""] ?? clip.start_snap ?? "—"} → {SNAP_LABEL[clip.end_snap ?? ""] ?? clip.end_snap ?? "—"}
            </span>
            {loop && <span className="tag">반복 중 (L)</span>}
          </div>
          <BoundaryStrip
            view={view}
            inT={inT}
            outT={outT}
            playhead={t}
            cuts={analysis.data?.scenes ?? []}
            silences={analysis.data?.silences ?? []}
            waveform={waveform.data}
            fps={fps}
            disabled={readOnly}
            onSeek={seek}
            onChange={setRange}
          />
          {clip.needs_manual_range && (
            <p className="small warn-text">
              구간을 직접 정해 주세요: 재생 위치에서 I(시작), O(끝).
              {clip.suggested_start != null && (
                <>
                  {" "}정렬 제안{" "}
                  <button className="link" onClick={() => seek(clip.suggested_start!)}>{timecode(clip.suggested_start, fps)}</button>
                  {clip.suggested_end != null && !readOnly && (
                    <>
                      {" "}
                      <button className="btn btn-small" onClick={() => setRange(clip.suggested_start!, clip.suggested_end!)}>제안 구간 사용</button>
                    </>
                  )}
                </>
              )}
            </p>
          )}
          <details className="small keys">
            <summary>단축키</summary>
            <dl>
              {KEYS.map(([k, d]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{d}</dd>
                </div>
              ))}
            </dl>
          </details>
          <ScriptPanel sentences={sentences.data ?? []} t={t} fps={fps} seek={seek} />
        </section>

        <section className="editor-right">
          <ThumbnailPanel clip={clip} readOnly={readOnly} currentTime={t} run={run} />
          {clip.metadata ? (
            <MetadataForm key={clip.metadata.generated_at ?? ""} clip={clip} meta={clip.metadata} readOnly={readOnly} publishEditable={PUBLISH_EDITABLE.includes(clip.status) && !holder} save={(p) => run(patch.mutateAsync(p))} saving={patch.isPending} />
          ) : (
            <p className="muted small">메타데이터는 구간을 정하면 만들어집니다.</p>
          )}
          <div className="approve-box">
            {warnings.length > 0 && (
              <div>
                <ul className="small warn-text">
                  {warnings.map((w) => <li key={w}>{w}</li>)}
                </ul>
                <label className="small">
                  <input
                    type="checkbox"
                    checked={clip.warnings_acknowledged}
                    disabled={readOnly}
                    onChange={(e) => void run(patch.mutateAsync({ warnings_acknowledged: e.target.checked }))}
                  />{" "}
                  경고를 확인했습니다
                </label>
              </div>
            )}
            {editable && clip.approval_problems.length > 0 && (
              <p className="small muted">승인 전에: {clip.approval_problems.join(" · ")}</p>
            )}
            {clip.reject_reason && <p className="small">반려 사유: {clip.reject_reason}</p>}
            <RenderStatus clip={clip} busy={action.isPending} onRender={() => void run(action.mutateAsync({ action: "render" }))} />
            <PublishStatus clip={clip} run={run} />
            <div className="approve-buttons">
              {editable ? (
                <>
                  <button className="btn btn-primary" disabled={readOnly || action.isPending || clip.approval_problems.length > 0} onClick={() => void approve()}>
                    승인 (Ctrl+Enter)
                  </button>
                  <button
                    className="btn"
                    disabled={readOnly || action.isPending}
                    onClick={() => {
                      const reason = window.prompt("반려 사유", "");
                      if (reason !== null) void run(action.mutateAsync({ action: "reject", reason }));
                    }}
                  >
                    반려
                  </button>
                </>
              ) : (
                REOPENABLE.includes(clip.status) && (
                  <button
                    className="btn"
                    disabled={action.isPending}
                    onClick={() => {
                      if (!clip.render_url || window.confirm("다시 열면 렌더한 파일을 지우고 대기 중인 업로드를 취소합니다. 계속할까요?"))
                        void run(action.mutateAsync({ action: "reopen" }));
                    }}
                  >
                    다시 열기
                  </button>
                )
              )}
            </div>
          </div>
          <AuditList clipId={clip.id} version={clip.edited_at ?? ""} />
        </section>
      </div>
    </div>
  );
}

function ScriptPanel({ sentences, t, fps, seek }: { sentences: AlignedSentence[]; t: number; fps: number; seek: (t: number) => void }) {
  const current = sentences.find((s) => s.start_sec <= t && t <= s.end_sec + 0.3);
  const ref = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const el = ref.current?.querySelector(".current");
    el?.scrollIntoView({ block: "nearest" });
  }, [current?.seq]);
  if (!sentences.length) return <p className="muted small">정렬된 원고 문장이 없습니다.</p>;
  return (
    <ol ref={ref} className="script-panel">
      {sentences.map((s) => (
        <li
          key={s.seq}
          className={`${s === current ? "current" : ""} ${s.score < LOW_SCORE ? "low" : ""}`}
          onClick={() => seek(s.start_sec)}
          title={`정렬 점수 ${s.score.toFixed(2)}`}
        >
          <span className="muted small">
            {timecode(s.start_sec, fps)} · {SENTENCE_KIND_LABEL[s.kind] ?? s.kind}
          </span>
          <div>{s.text}</div>
        </li>
      ))}
    </ol>
  );
}

function ThumbnailPanel({ clip, readOnly, currentTime, run }: { clip: ClipDetail; readOnly: boolean; currentTime: number; run: (p: Promise<unknown>) => Promise<void> }) {
  const thumbs = useThumbnailActions(clip.id);
  const file = useRef<HTMLInputElement>(null);
  return (
    <div className="thumb-panel">
      <h2>썸네일</h2>
      {clip.thumbnail_url ? (
        <img className="thumb-final" src={clip.thumbnail_url} alt="업로드할 썸네일" />
      ) : (
        <p className="muted small">아직 없습니다</p>
      )}
      {clip.kind === "brief" && <p className="small muted">단신: 하단 자막이 처음 뜬 화면을 그대로 씁니다</p>}
      <div className="thumb-grid">
        {clip.thumbnails.map((th) => (
          <button
            key={th.id}
            className={`thumb${th.selected ? " selected" : ""}`}
            disabled={readOnly}
            onClick={() => void run(thumbs.select.mutateAsync(th.id))}
            title={th.source === "candidate" ? `후보 ${th.rank} · 점수 ${th.score?.toFixed(2)}` : th.source === "frame" ? "직접 고른 프레임" : "올린 이미지"}
          >
            <img src={th.url} alt="" loading="lazy" />
            <span className="small">
              {th.source === "candidate" ? th.rank : th.source === "frame" ? "프레임" : "업로드"}
              {th.details?.rule ? " · 첫 자막" : ""}
            </span>
          </button>
        ))}
      </div>
      {!readOnly && (
        <div className="thumb-actions">
          <button className="btn btn-small" disabled={thumbs.frame.isPending} onClick={() => void run(thumbs.frame.mutateAsync(currentTime))}>
            현재 프레임 사용
          </button>
          <button className="btn btn-small" onClick={() => file.current?.click()}>이미지 올리기</button>
          <input
            ref={file}
            type="file"
            accept="image/*"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void run(thumbs.upload.mutateAsync(f));
              e.target.value = "";
            }}
          />
        </div>
      )}
    </div>
  );
}

function splitList(v: string): string[] {
  return v.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
}

function MetadataForm({ clip, meta, readOnly, publishEditable, save, saving }: { clip: ClipDetail; meta: ClipMetadata; readOnly: boolean; publishEditable: boolean; save: (p: ClipPatch) => Promise<void>; saving: boolean }) {
  const channels = useChannels();
  const initial = useMemo(
    () => ({
      title: meta.title,
      description: meta.description,
      tags: meta.tags.join(", "),
      hashtags: meta.hashtags.join(" "),
      thumbnail_text: meta.thumbnail_text,
      privacy: meta.privacy,
      publish_at: toKstInput(meta.publish_at),
      channel_ids: [...meta.channel_ids].sort().join(","),
    }),
    [meta],
  );
  const [f, setF] = useState(initial);
  const dirty = JSON.stringify(f) !== JSON.stringify(initial);
  const [previewText, setPreviewText] = useState(f.thumbnail_text);
  useEffect(() => {
    const timer = window.setTimeout(() => setPreviewText(f.thumbnail_text), 400);
    return () => window.clearTimeout(timer);
  }, [f.thumbnail_text]);

  const publishOnly = readOnly && publishEditable;
  const submit = useCallback(() => {
    if (!dirty || (readOnly && !publishEditable)) return;
    const p: ClipPatch = {};
    if (f.title !== initial.title) p.title = f.title;
    if (f.description !== initial.description) p.description = f.description;
    if (f.tags !== initial.tags) p.tags = splitList(f.tags);
    if (f.hashtags !== initial.hashtags) p.hashtags = f.hashtags.split(/\s+/).filter(Boolean);
    if (f.thumbnail_text !== initial.thumbnail_text) p.thumbnail_text = f.thumbnail_text;
    if (f.privacy !== initial.privacy) p.privacy = f.privacy;
    if (f.publish_at !== initial.publish_at) {
      if (f.publish_at) p.publish_at = fromKstInput(f.publish_at);
      else p.clear_publish_at = true;
    }
    if (f.channel_ids !== initial.channel_ids) p.channel_ids = f.channel_ids ? f.channel_ids.split(",").map(Number) : [];
    if (readOnly) {
      // After approval only the publishing settings can change.
      for (const k of ["title", "description", "tags", "hashtags", "thumbnail_text"] as const) delete p[k];
    }
    void save(p);
  }, [dirty, f, initial, readOnly, publishEditable, save]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        submit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [submit]);

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: e.target.value });

  return (
    <form className="meta-form" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <h2>메타데이터</h2>
      <label>
        제목 <span className={f.title.length > TITLE_MAX ? "error-text" : "muted"}>{f.title.length}/{TITLE_MAX}</span>
        <input value={f.title} onChange={set("title")} disabled={readOnly} />
      </label>
      <ul className="candidates small">
        {meta.title_candidates.map((c) => (
          <li key={c.text}>
            <button type="button" className="link" disabled={readOnly} onClick={() => setF({ ...f, title: c.text })}>
              {c.source === "llm" ? "LLM" : "원고"}
            </button>{" "}
            {c.text}
            {c.warnings.map((w) => <div key={w} className="warn-text">{w}</div>)}
          </li>
        ))}
      </ul>
      {clip.kind === "report" && (
        <label>
          썸네일 문구 (줄바꿈으로 두 줄)
          <textarea rows={2} value={f.thumbnail_text} onChange={set("thumbnail_text")} disabled={readOnly} />
          {previewText !== initial.thumbnail_text && (
            <img className="thumb-final" src={`/api/clips/${clip.id}/thumbnail-preview?text=${encodeURIComponent(previewText)}`} alt="문구 미리보기" />
          )}
        </label>
      )}
      <label>
        설명
        <textarea rows={6} value={f.description} onChange={set("description")} disabled={readOnly} />
      </label>
      {meta.summary && (
        <p className="small">
          <button type="button" className="link" disabled={readOnly} onClick={() => setF({ ...f, description: `${f.description}\n\n${meta.summary}` })}>
            요약 넣기
          </button>{" "}
          <span className="muted">{meta.summary}</span>
        </p>
      )}
      <label>
        태그 (쉼표로 구분)
        <input value={f.tags} onChange={set("tags")} disabled={readOnly} />
      </label>
      <label>
        해시태그
        <input value={f.hashtags} onChange={set("hashtags")} disabled={readOnly} />
      </label>
      <div className="row">
        <label>
          공개 설정
          <select value={f.privacy} onChange={set("privacy")} disabled={!publishEditable}>
            <option value="private">비공개</option>
            <option value="unlisted">일부 공개</option>
            <option value="public">공개</option>
          </select>
        </label>
        <label>
          예약 (KST, 비우면 처리 후 바로)
          <input type="datetime-local" value={f.publish_at} onChange={set("publish_at")} disabled={!publishEditable} />
        </label>
      </div>
      <ChannelPicker
        channels={channels.data?.channels ?? []}
        value={f.channel_ids}
        disabled={!publishEditable}
        onChange={(v) => setF({ ...f, channel_ids: v })}
      />
      {publishOnly && <p className="small muted">승인한 뒤에는 발행 설정(채널·공개·예약)만 바꿀 수 있습니다.</p>}
      <button className="btn" type="submit" disabled={!dirty || (readOnly && !publishEditable) || saving}>
        {saving ? "저장 중…" : dirty ? "저장 (Ctrl+S)" : "저장됨"}
      </button>
    </form>
  );
}

function ChannelPicker({ channels, value, disabled, onChange }: { channels: Channel[]; value: string; disabled: boolean; onChange: (v: string) => void }) {
  const selected = value ? value.split(",").map(Number) : [];
  const shown = channels.filter((c) => c.status === "active" || selected.includes(c.id));
  const toggle = (id: number, on: boolean) =>
    onChange((on ? [...selected, id] : selected.filter((x) => x !== id)).sort((a, b) => a - b).join(","));
  return (
    <fieldset className="channel-picker">
      <legend className="small">업로드 채널</legend>
      {shown.length === 0 ? (
        <span className="small muted">
          연결된 채널이 없습니다 (<Link to="/channels">채널 연결</Link>). 채널 없이 승인하면 렌더까지만 합니다.
        </span>
      ) : (
        shown.map((c) => (
          <label key={c.id} className="small inline">
            <input type="checkbox" checked={selected.includes(c.id)} disabled={disabled || c.status !== "active"} onChange={(e) => toggle(c.id, e.target.checked)} /> {c.name}
            {c.status !== "active" && <span className="warn-text"> (다시 연결 필요)</span>}
          </label>
        ))
      )}
    </fieldset>
  );
}

const ACTION_LABEL: Record<string, string> = {
  range: "구간",
  metadata: "메타데이터",
  thumbnail: "썸네일 선택",
  thumbnail_add: "썸네일 추가",
  acknowledge: "경고 확인",
  approve: "승인",
  reject: "반려",
  reopen: "다시 열기",
  render: "다시 렌더",
  publish: "업로드",
  publish_retry: "업로드 다시 시도",
  publish_cancel: "업로드 취소",
};

function AuditList({ clipId, version }: { clipId: number; version: string }) {
  const audit = useAudit(clipId, version);
  if (!audit.data?.length) return null;
  return (
    <details className="small audit">
      <summary>변경 기록 ({audit.data.length})</summary>
      <ul>
        {audit.data.map((a) => (
          <li key={a.id}>
            {new Date(a.created_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })} · {a.user_name ?? "?"} ·{" "}
            {ACTION_LABEL[a.action] ?? a.action} <span className="muted">{Object.keys(a.diff).join(", ")}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** The final render (M6): progress, the result to watch and download, or the error. */
function RenderStatus({ clip, busy, onRender }: { clip: ClipDetail; busy: boolean; onRender: () => void }) {
  if (!["APPROVED", "RENDERING", "RENDERED"].includes(clip.status) && !clip.render_url) return null;
  if (clip.render_error)
    return (
      <div className="render-box">
        <p className="error-box">렌더 실패: {clip.render_error}</p>
        <button className="btn btn-small" disabled={busy} onClick={onRender}>다시 렌더</button>
      </div>
    );
  if (!clip.render_url)
    return <p className="small muted render-box">원본 화질로 렌더하는 중입니다…</p>;
  return (
    <div className="render-box">
      <h2>렌더 결과</h2>
      <video className="render-video" src={clip.render_url} controls preload="metadata" />
      <div className="small">
        <a href={`/media/clips/${clip.id}/final.mp4?download=1`}>영상 내려받기</a>
        {clip.thumbnail_url && (
          <>
            {" · "}
            <a href={clip.thumbnail_url} download={`${clip.item_no}_thumbnail.jpg`}>썸네일 내려받기</a>
          </>
        )}
        {" · "}
        <button className="link" disabled={busy} onClick={onRender}>다시 렌더</button>
      </div>
    </div>
  );
}

/** Uploads of the clip (M7): one row per channel, and "업로드" when it has not been sent yet. */
function PublishStatus({ clip, run }: { clip: ClipDetail; run: (p: Promise<unknown>) => Promise<void> }) {
  const publish = usePublishClip(clip.id);
  const channels = clip.metadata?.channel_ids ?? [];
  const pending = channels.filter((id) => !clip.publish_tasks.some((t) => t.channel_id === id && t.status !== "FAILED"));
  const canPublish = ["RENDERED", "UPLOAD_FAILED"].includes(clip.status) && pending.length > 0;
  if (!clip.publish_tasks.length && !canPublish) {
    if (clip.status === "RENDERED")
      return <p className="small muted">업로드 채널을 고르지 않아 렌더까지만 했습니다. 채널을 고르고 저장하면 업로드할 수 있습니다.</p>;
    return null;
  }
  return (
    <div className="publish-box">
      <h2>업로드</h2>
      {clip.publish_tasks.map((t) => (
        <TaskCard key={t.id} task={t} />
      ))}
      {canPublish && (
        <button className="btn btn-primary btn-small" disabled={publish.isPending} onClick={() => void run(publish.mutateAsync())}>
          {clip.status === "UPLOAD_FAILED" ? "다시 업로드" : `${pending.length}개 채널에 업로드`}
        </button>
      )}
    </div>
  );
}
