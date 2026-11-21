import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useMetrics, usePatchProgram, useProgramOverlay, useRunCleanup, useStorage } from "../api/admin";
import { useCreateProgram, usePrograms } from "../api/jobs";
import { useChannels } from "../api/publish";
import type { Program, ProgramPatch } from "../api/types";
import { formatBytes, formatDuration } from "../labels";

type Tab = "programs" | "storage" | "metrics";
const TABS: [Tab, string][] = [
  ["programs", "프로그램"],
  ["storage", "저장 공간"],
  ["metrics", "운영 지표"],
];

/** Administration (M9): program profiles, storage and retention cleanup, operating metrics. */
export function AdminPage() {
  const [tab, setTab] = useState<Tab>(() => (localStorage.getItem("news-clipper.admin-tab") as Tab) || "programs");
  useEffect(() => {
    try {
      localStorage.setItem("news-clipper.admin-tab", tab);
    } catch {
      // private mode
    }
  }, [tab]);
  return (
    <div className="admin-page">
      <h1>관리</h1>
      <div className="row tabs">
        {TABS.map(([t, label]) => (
          <button key={t} className={`btn btn-small${tab === t ? " btn-primary" : ""}`} onClick={() => setTab(t)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "programs" && <Programs />}
      {tab === "storage" && <Storage />}
      {tab === "metrics" && <Metrics />}
    </div>
  );
}

// --- programs ---

function Programs() {
  const programs = usePrograms();
  const create = useCreateProgram();
  const [selected, setSelected] = useState<number | null>(null);
  const list = programs.data ?? [];
  const current = list.find((p) => p.id === selected) ?? list[0];
  return (
    <div className="admin-programs">
      <div className="row">
        {list.map((p) => (
          <button key={p.id} className={`btn btn-small${current?.id === p.id ? " btn-primary" : ""}`} onClick={() => setSelected(p.id)}>
            {p.name}
          </button>
        ))}
        <button
          className="btn btn-small"
          disabled={create.isPending}
          onClick={() => {
            const name = window.prompt("새 프로그램 이름 (MARS-L 큐시트의 프로그램 이름과 같게)");
            if (name?.trim()) create.mutate(name.trim(), { onSuccess: (p) => setSelected(p.id), onError: (e) => window.alert((e as Error).message) });
          }}
        >
          + 프로그램 추가
        </button>
      </div>
      {current ? <ProgramForm key={current.id} program={current} others={list.filter((p) => p.id !== current.id)} /> : <p className="muted">프로그램이 없습니다.</p>}
    </div>
  );
}

function lines(map: Record<string, string>): string {
  return Object.entries(map)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}
function parseLines(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const [k, ...rest] = line.split("=");
    if (k?.trim() && rest.join("=").trim()) out[k.trim()] = rest.join("=").trim();
  }
  return out;
}
function list(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function ProgramForm({ program, others }: { program: Program; others: Program[] }) {
  const patch = usePatchProgram(program.id);
  const overlay = useProgramOverlay(program.id);
  const channels = useChannels();
  const file = useRef<HTMLInputElement>(null);
  const initial = useMemo(
    () => ({
      name: program.name,
      broadcast_time: program.broadcast_time?.slice(0, 5) ?? "",
      audio_track: program.audio_track == null ? "" : String(program.audio_track),
      dedupe_program: program.dedupe_program ?? "",
      excluded_sources: program.excluded_sources.join(", "),
      title_template: program.title_template ?? "",
      description_footer: program.description_footer ?? "",
      default_tags: program.default_tags.join(", "),
      pronunciations: lines(program.pronunciations ?? {}),
      default_channels: [...(program.default_channels ?? [])].sort().join(","),
    }),
    [program],
  );
  const [f, setF] = useState(initial);
  const [message, setMessage] = useState<string | null>(null);
  const [previewVersion, setPreviewVersion] = useState(0);
  const dirty = JSON.stringify(f) !== JSON.stringify(initial);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const chosen = f.default_channels ? f.default_channels.split(",").map(Number) : [];
  const hasOverlay = Boolean((program.thumbnail_template?.report as Record<string, unknown> | undefined)?.overlay_image);

  const save = () => {
    const p: ProgramPatch = {};
    if (f.name !== initial.name) p.name = f.name;
    if (f.broadcast_time !== initial.broadcast_time) p.broadcast_time = f.broadcast_time ? `${f.broadcast_time}:00` : null;
    if (f.audio_track !== initial.audio_track) p.audio_track = f.audio_track === "" ? null : Number(f.audio_track);
    if (f.dedupe_program !== initial.dedupe_program) p.dedupe_program = f.dedupe_program;
    if (f.excluded_sources !== initial.excluded_sources) p.excluded_sources = list(f.excluded_sources);
    if (f.title_template !== initial.title_template) p.title_template = f.title_template;
    if (f.description_footer !== initial.description_footer) p.description_footer = f.description_footer;
    if (f.default_tags !== initial.default_tags) p.default_tags = list(f.default_tags);
    if (f.pronunciations !== initial.pronunciations) p.pronunciations = parseLines(f.pronunciations);
    if (f.default_channels !== initial.default_channels) p.default_channels = chosen;
    setMessage(null);
    patch.mutate(p, { onSuccess: () => setMessage("저장했습니다"), onError: (e) => setMessage((e as Error).message) });
  };

  return (
    <form className="meta-form program-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <div className="row">
        <label>
          이름 (MARS-L 프로그램 이름)
          <input value={f.name} onChange={set("name")} />
        </label>
        <label>
          방송 시각 (큐시트가 여러 개일 때)
          <input type="time" value={f.broadcast_time} onChange={set("broadcast_time")} />
        </label>
        <label>
          오디오 트랙 (비우면 기본)
          <input type="number" min={0} value={f.audio_track} onChange={set("audio_track")} style={{ width: "6em" }} />
        </label>
      </div>
      <div className="row">
        <label>
          전날 방송분 제외 (이 프로그램에서 전날 나간 기사)
          <select value={f.dedupe_program} onChange={set("dedupe_program")}>
            <option value="">사용 안 함</option>
            {others.map((o) => (
              <option key={o.id} value={o.name}>{o.name}</option>
            ))}
            {f.dedupe_program && !others.some((o) => o.name === f.dedupe_program) && <option value={f.dedupe_program}>{f.dedupe_program}</option>}
          </select>
        </label>
        <label>
          제외할 방송사 (쉼표로 구분)
          <input value={f.excluded_sources} onChange={set("excluded_sources")} />
        </label>
      </div>
      <label>
        유튜브 제목 템플릿 <span className="muted">(비우면 기본 {"{title}[{station} {program}]"} · 쓸 수 있는 칸: {"{title} {station} {program} {date} {reporter}"})</span>
        <input value={f.title_template} onChange={set("title_template")} placeholder="{title}[{station} {program}]" />
      </label>
      <label>
        설명 꼬리말 <span className="muted">(비우면 꼬리말 없음 · {"{reporter_credit} {station} {program} {date}"})</span>
        <textarea rows={2} value={f.description_footer} onChange={set("description_footer")} />
      </label>
      <label>
        기본 태그 (쉼표로 구분)
        <input value={f.default_tags} onChange={set("default_tags")} />
      </label>
      <label>
        발음 사전 (정렬용, 한 줄에 하나: 표기=읽는 소리, 예: KIA=기아)
        <textarea rows={3} value={f.pronunciations} onChange={set("pronunciations")} />
      </label>
      <fieldset className="channel-picker">
        <legend className="small">기본 업로드 채널 (새 클립에 적용)</legend>
        {(channels.data?.channels ?? []).filter((c) => c.status === "active" || chosen.includes(c.id)).map((c) => (
          <label key={c.id} className="small inline">
            <input
              type="checkbox"
              checked={chosen.includes(c.id)}
              onChange={(e) => setF({ ...f, default_channels: (e.target.checked ? [...chosen, c.id] : chosen.filter((x) => x !== c.id)).sort((a, b) => a - b).join(",") })}
            />{" "}
            {c.name}
          </label>
        ))}
        {!channels.data?.channels.length && <span className="small muted">연결된 채널이 없습니다 (<Link to="/channels">채널</Link>)</span>}
      </fieldset>
      <div className="row">
        <button className="btn btn-primary" type="submit" disabled={!dirty || patch.isPending}>
          {patch.isPending ? "저장 중…" : dirty ? "저장" : "저장됨"}
        </button>
        {message && <span className="small">{message}</span>}
      </div>

      <h2>리포트 썸네일 템플릿</h2>
      <p className="small muted">16:9 투명 PNG(로고, 띠 등). 대표 프레임 위에 깔고 그 위에 제목 두 줄을 씁니다. 단신은 템플릿을 쓰지 않습니다.</p>
      <img className="thumb-final" src={`/api/programs/${program.id}/thumbnail-preview?v=${previewVersion}`} alt="템플릿 미리보기" />
      <div className="row">
        <button type="button" className="btn btn-small" onClick={() => file.current?.click()} disabled={overlay.upload.isPending}>
          {hasOverlay ? "템플릿 바꾸기" : "템플릿 올리기"}
        </button>
        {hasOverlay && (
          <button type="button" className="btn btn-small" disabled={overlay.remove.isPending} onClick={() => overlay.remove.mutate(undefined, { onSuccess: () => setPreviewVersion((v) => v + 1) })}>
            템플릿 지우기 (기본 디자인)
          </button>
        )}
        <input
          ref={file}
          type="file"
          accept="image/png"
          hidden
          onChange={(e) => {
            const picked = e.target.files?.[0];
            if (picked) overlay.upload.mutate(picked, { onSuccess: () => setPreviewVersion((v) => v + 1), onError: (err) => window.alert((err as Error).message) });
            e.target.value = "";
          }}
        />
      </div>
    </form>
  );
}

// --- storage ---

const KIND_LABEL: Record<string, string> = { source: "원본", work: "작업 파일", render: "렌더", upload: "미완료 업로드", uploads: "업로드 중", other: "기타(썸네일 등)" };

function Storage() {
  const storage = useStorage();
  const run = useRunCleanup();
  const [result, setResult] = useState<string | null>(null);
  const s = storage.data;
  if (!s) return <p className="muted">불러오는 중…</p>;
  const total = Object.values(s.usage).reduce((a, b) => a + b, 0);
  return (
    <div>
      <h2>사용량 {formatBytes(total)}</h2>
      <table className="table small">
        <tbody>
          {Object.entries(s.usage).map(([k, v]) => (
            <tr key={k}>
              <td>{KIND_LABEL[k] ?? k}</td>
              <td>{formatBytes(v)}</td>
              <td style={{ width: "50%" }}>
                <meter min={0} max={total || 1} value={v} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>보존 기간 정리</h2>
      <p className="small">
        원본 {s.retention.source_days}일 · 작업 파일(프록시·정렬 오디오) {s.retention.work_days}일 · 렌더는 업로드가 모두 게시된 뒤 {s.retention.render_days}일 · 작업이 되지 않은 업로드 {s.retention.upload_hours}시간.{" "}
        {s.retention.enabled ? (
          <strong>매일 03:17에 자동으로 정리합니다.</strong>
        ) : (
          <span className="warn-text">자동 정리가 꺼져 있습니다(서버 설정 RETENTION_ENABLED). 아래 목록은 지금 정리하면 지울 것입니다.</span>
        )}
      </p>
      <p className="small muted">기간은 서버 설정(RETENTION_*_DAYS)으로 바꿉니다. 원본을 지우면 그 방송의 클립은 다시 렌더할 수 없습니다(YouTube에 올라간 영상은 그대로).</p>
      {s.plan_count === 0 ? (
        <p className="muted">지금 지울 것이 없습니다.</p>
      ) : (
        <>
          <p>
            {s.plan_count}건, {formatBytes(s.plan_bytes)}{" "}
            <button
              className="btn btn-small"
              disabled={run.isPending}
              onClick={() => {
                if (window.confirm(`${s.plan_count}건(${formatBytes(s.plan_bytes)})을 지웁니다. 되돌릴 수 없습니다. 계속할까요?`))
                  run.mutate(undefined, {
                    onSuccess: (r) => setResult(`${r.deleted.length}건, ${formatBytes(r.bytes)}을 지웠습니다${r.errors.length ? ` (오류 ${r.errors.length}건: ${r.errors.join(" / ")})` : ""}`),
                    onError: (e) => setResult((e as Error).message),
                  });
              }}
            >
              지금 정리
            </button>
          </p>
          <table className="table small">
            <thead>
              <tr>
                <th>종류</th>
                <th>대상</th>
                <th>크기</th>
                <th>이유</th>
              </tr>
            </thead>
            <tbody>
              {s.plan.map((i, n) => (
                <tr key={n}>
                  <td>{KIND_LABEL[i.kind] ?? i.kind}</td>
                  <td>
                    {i.job_id ? <Link to={i.clip_id ? `/jobs/${i.job_id}/clips/${i.clip_id}` : `/jobs/${i.job_id}`}>작업 {i.job_id}{i.clip_id ? ` · 클립 ${i.clip_id}` : ""}</Link> : i.upload_id?.slice(0, 8)}
                  </td>
                  <td>{formatBytes(i.bytes)}</td>
                  <td>{i.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {result && <p className="ok-box">{result}</p>}
    </div>
  );
}

// --- metrics ---

const STEP_LABEL: Record<string, string> = { proxy: "프록시", analyze: "오디오·무음", newsroom: "큐시트", align: "정렬", metadata: "메타데이터", thumbnails: "썸네일" };

function secs(v: number | null | undefined): string {
  if (v == null) return "—";
  return v < 60 ? `${v.toFixed(v < 10 ? 1 : 0)}초` : formatDuration(v);
}

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function Metrics() {
  const [days, setDays] = useState(30);
  const m = useMetrics(days);
  const t = m.data?.totals;
  return (
    <div>
      <div className="row small">
        최근{" "}
        {[7, 30, 90].map((d) => (
          <button key={d} className={`btn btn-small${days === d ? " btn-primary" : ""}`} onClick={() => setDays(d)}>
            {d}일
          </button>
        ))}
      </div>
      {!t ? (
        <p className="muted">불러오는 중…</p>
      ) : (
        <>
          <div className="summary-cards">
            <Card title="방송" value={String(t.jobs)} sub={`업로드 대상 기사 ${t.clips}건`} />
            <Card title="자동 검출률" value={pct(t.detection_rate)} sub={`정렬이 구간을 찾은 기사 ${t.auto}/${t.clips}`} />
            <Card title="경계 수정률" value={pct(t.boundary_edit_rate)} sub={`자동 구간 중 검수자가 옮긴 것 ${t.auto_moved}/${t.auto}`} />
            <Card title="제목 수정률" value={pct(t.title_edit_rate)} sub={`승인한 기사 중 ${t.titles_edited}건`} />
            <Card title="업로드 성공률" value={pct(t.upload_success_rate)} sub={`게시 ${t.published} · 예약·처리 중 ${t.on_platform} · 실패 ${t.upload_failed} · 재시도 ${t.upload_retries}`} />
            <Card title="처리 시간(중앙값)" value={secs(t.median_pipeline_seconds)} sub="업로드 → 검수 대기" />
            <Card title="LLM 토큰" value={(Number(t.llm_input_tokens) + Number(t.llm_output_tokens)).toLocaleString()} sub={`입력 ${Number(t.llm_input_tokens).toLocaleString()} · 출력 ${Number(t.llm_output_tokens).toLocaleString()}`} />
          </div>
          <p className="small muted">
            단계별 처리 시간(중앙값):{" "}
            {Object.entries(t.median_step_seconds).map(([k, v]) => `${STEP_LABEL[k] ?? k} ${secs(v)}`).join(" · ") || "—"}
          </p>
          <table className="table small">
            <thead>
              <tr>
                <th>방송</th>
                <th>길이</th>
                <th>처리</th>
                <th>기사</th>
                <th>자동 / 경계 수정</th>
                <th>수동 / 구간 없음</th>
                <th>승인 / 반려</th>
                <th>제목 수정</th>
                <th>게시 / 실패</th>
              </tr>
            </thead>
            <tbody>
              {m.data!.jobs.map((j) => (
                <tr key={j.job_id}>
                  <td>
                    <Link to={`/jobs/${j.job_id}`}>{j.broadcast_date ?? "—"} {j.program ?? ""}</Link>
                  </td>
                  <td>{formatDuration(j.duration)}</td>
                  <td>{secs(j.pipeline_seconds)}</td>
                  <td>{j.clips}</td>
                  <td>{j.auto} / {j.auto_moved}</td>
                  <td>{j.manual} / {j.no_range}</td>
                  <td>{j.approved} / {j.rejected}</td>
                  <td>{j.titles_edited}</td>
                  <td>{j.published} / {j.upload_failed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function Card({ title, value, sub }: { title: string; value: string; sub: string }) {
  return (
    <div className="summary-card">
      <span className="small muted">{title}</span>
      <strong className="metric-value">{value}</strong>
      <span className="small">{sub}</span>
    </div>
  );
}
