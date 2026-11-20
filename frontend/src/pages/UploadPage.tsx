import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as tus from "tus-js-client";

import { ApiError } from "../api/client";
import { useCreateJob, useCreateProgram, usePrograms } from "../api/jobs";
import { RundownPreview } from "../components/RundownPreview";
import { formatBytes, todayKst } from "../labels";

const ACCEPT = [".mp4", ".mov", ".mxf", ".mkv", ".ts"];
const CHUNK_SIZE = 50 * 1024 * 1024; // nginx accepts 100 MB per request

type Phase = "idle" | "uploading" | "paused" | "creating" | "error";

interface Progress {
  sent: number;
  total: number;
  speed: number | null; // bytes/s
}

export function UploadPage() {
  const navigate = useNavigate();
  const programs = usePrograms();
  const createJob = useCreateJob();

  const [file, setFile] = useState<File | null>(null);
  const [programId, setProgramId] = useState<number | null>(null);
  const [broadcastDate, setBroadcastDate] = useState(todayKst());
  const [cueSheetId, setCueSheetId] = useState<number | null>(null); // picked by hand; null = automatic
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resumed, setResumed] = useState(false);
  const uploadRef = useRef<tus.Upload | null>(null);
  const samples = useRef<{ t: number; bytes: number }[]>([]);

  // Pick the first program by default once loaded.
  useEffect(() => {
    if (programId === null && programs.data?.length) setProgramId(programs.data[0].id);
  }, [programs.data, programId]);

  // Stop an in-flight upload when leaving the page (it can be resumed later).
  useEffect(() => () => void uploadRef.current?.abort(), []);

  const busy = phase === "uploading" || phase === "creating";

  const chooseFile = (f: File | null) => {
    if (busy) return;
    setError(null);
    if (f && !ACCEPT.some((ext) => f.name.toLowerCase().endsWith(ext))) {
      setError(`지원하지 않는 형식입니다: ${f.name} (가능: ${ACCEPT.join(", ")})`);
      return;
    }
    setFile(f);
    setProgress(null);
    setPhase("idle");
  };

  const finish = (uploadUrl: string) => {
    const uploadId = uploadUrl.split("/").pop()!;
    setPhase("creating");
    createJob.mutate(
      { upload_id: uploadId, program_id: programId, broadcast_date: broadcastDate, cue_sheet_id: cueSheetId },
      {
        onSuccess: (job) => navigate(`/jobs/${job.id}`),
        onError: (e) => {
          setPhase("error");
          setError(`작업을 만들지 못했습니다: ${e instanceof ApiError ? e.message : String(e)}`);
        },
      },
    );
  };

  const start = async () => {
    if (!file) return;
    setError(null);
    samples.current = [];
    const upload = new tus.Upload(file, {
      endpoint: "/api/uploads",
      chunkSize: CHUNK_SIZE,
      retryDelays: [0, 1000, 3000, 5000, 10000, 20000],
      metadata: { filename: file.name, filetype: file.type || "application/octet-stream" },
      removeFingerprintOnSuccess: true,
      onProgress: (sent, total) => {
        const now = performance.now();
        const buf = samples.current;
        buf.push({ t: now, bytes: sent });
        while (buf.length > 2 && now - buf[0].t > 10_000) buf.shift(); // 10 s window
        const first = buf[0];
        const speed = now > first.t ? ((sent - first.bytes) / (now - first.t)) * 1000 : null;
        setProgress({ sent, total, speed });
      },
      onSuccess: () => finish(upload.url!),
      onError: (e) => {
        setPhase("error");
        setError(`업로드가 실패했습니다: ${e.message}. 다시 시작하면 이어서 올립니다.`);
      },
    });
    uploadRef.current = upload;
    // Same file uploaded before (interrupted)? Continue where it stopped.
    const previous = await upload.findPreviousUploads();
    setResumed(previous.length > 0);
    if (previous.length > 0) upload.resumeFromPreviousUpload(previous[0]);
    setPhase("uploading");
    upload.start();
  };

  const pause = async () => {
    await uploadRef.current?.abort();
    setPhase("paused");
  };

  const resume = () => {
    samples.current = [];
    setPhase("uploading");
    uploadRef.current?.start();
  };

  const pct = progress && progress.total > 0 ? (progress.sent / progress.total) * 100 : 0;
  const eta =
    progress?.speed && progress.speed > 0 ? (progress.total - progress.sent) / progress.speed : null;

  return (
    <section className="upload">
      <div className="page-head">
        <h1>녹화본 업로드</h1>
      </div>

      <DropZone file={file} disabled={busy || phase === "paused"} onFile={chooseFile} />

      <div className="form-row">
        <label>
          프로그램
          <ProgramSelect
            value={programId}
            onChange={(id) => {
              setProgramId(id);
              setCueSheetId(null);
            }}
            disabled={busy}
          />
        </label>
        <label>
          방송일
          <input
            type="date"
            value={broadcastDate}
            onChange={(e) => {
              setBroadcastDate(e.target.value);
              setCueSheetId(null);
            }}
            disabled={busy}
            required
          />
        </label>
      </div>

      <RundownPreview
        programId={programId}
        date={broadcastDate}
        value={cueSheetId}
        onChange={setCueSheetId}
        disabled={busy || phase === "paused"}
        missingHint="업로드는 할 수 있습니다. 작업은 '큐시트 대기' 상태로 기다리고, 큐시트가 등록된 뒤 작업 화면에서 다시 조회합니다."
      />

      {progress && (
        <div className="upload-progress">
          <div className="bar">
            <div className="bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <div className="muted small">
            {pct.toFixed(1)}% · {formatBytes(progress.sent)} / {formatBytes(progress.total)}
            {phase === "uploading" && progress.speed != null && ` · ${formatBytes(progress.speed)}/s`}
            {phase === "uploading" && eta != null && ` · 남은 시간 ${formatEta(eta)}`}
            {resumed && " · 이전 업로드에서 이어서 올리는 중"}
          </div>
        </div>
      )}

      {error && <p className="error-box">{error}</p>}

      <div className="actions">
        {(phase === "idle" || phase === "error") && (
          <button className="btn btn-primary" disabled={!file || !broadcastDate} onClick={start}>
            {phase === "error" ? "다시 시작" : "업로드 시작"}
          </button>
        )}
        {phase === "uploading" && (
          <button className="btn" onClick={pause}>
            일시 정지
          </button>
        )}
        {phase === "paused" && (
          <button className="btn btn-primary" onClick={resume}>
            이어서 올리기
          </button>
        )}
        {phase === "creating" && <span className="muted">영상을 확인하고 작업을 만드는 중…</span>}
      </div>
    </section>
  );
}

function formatEta(seconds: number): string {
  const s = Math.round(seconds);
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}분 ${s % 60}초` : `${Math.floor(m / 60)}시간 ${m % 60}분`;
}

function DropZone(props: { file: File | null; disabled: boolean; onFile: (f: File | null) => void }) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  return (
    <div
      className={`dropzone${over ? " over" : ""}${props.disabled ? " disabled" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (!props.disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!props.disabled) props.onFile(e.dataTransfer.files[0] ?? null);
      }}
      onClick={() => !props.disabled && input.current?.click()}
    >
      <input
        ref={input}
        type="file"
        accept={ACCEPT.join(",")}
        hidden
        onChange={(e) => props.onFile(e.target.files?.[0] ?? null)}
      />
      {props.file ? (
        <>
          <strong>{props.file.name}</strong>
          <span className="muted">{formatBytes(props.file.size)}</span>
        </>
      ) : (
        <>
          <strong>녹화본을 여기에 끌어다 놓거나 클릭해서 고르세요</strong>
          <span className="muted">MP4, MOV, MXF, MKV, TS · 최대 50GB · 끊겨도 이어 올리기</span>
        </>
      )}
    </div>
  );
}

function ProgramSelect(props: { value: number | null; onChange: (id: number | null) => void; disabled: boolean }) {
  const programs = usePrograms();
  const create = useCreateProgram();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");

  if (adding) {
    return (
      <span className="inline-form">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 뉴스데스크" autoFocus />
        <button
          className="btn"
          disabled={!name.trim() || create.isPending}
          onClick={() =>
            create.mutate(name.trim(), {
              onSuccess: (p) => {
                props.onChange(p.id);
                setAdding(false);
                setName("");
              },
            })
          }
        >
          추가
        </button>
        <button className="btn" onClick={() => setAdding(false)}>
          취소
        </button>
        {create.isError && <span className="error-text small">{create.error.message}</span>}
      </span>
    );
  }
  return (
    <span className="inline-form">
      <select
        value={props.value ?? ""}
        disabled={props.disabled}
        onChange={(e) => props.onChange(e.target.value ? Number(e.target.value) : null)}
      >
        <option value="">(선택 안 함)</option>
        {programs.data?.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <button className="btn" disabled={props.disabled} onClick={() => setAdding(true)}>
        새 프로그램…
      </button>
    </span>
  );
}
