import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { useWaveform } from "../api/review";
import type { Clip, RundownItem } from "../api/types";
import { CLIP_FLAG_LABEL, CLIP_STATUS_LABEL, timecode } from "../labels";

interface SpriteInfo {
  interval: number;
  count: number;
  columns: number;
  rows: number;
  width: number;
  height: number;
}

const STRIP_H = 48;
const WAVE_H = 36;
const BLOCK_H = 26;

/** The whole recording (spec 5.8 작업 상세): thumbnails, waveform, cue sheet items where they were
 * found (upload targets coloured by review status, others grey) and warning marks. Click to seek,
 * double-click a clip block to open it in the editor. */
export function JobTimeline({
  jobId,
  duration,
  fps,
  items,
  clips,
  t,
  onSeek,
  onOpen,
}: {
  jobId: number;
  duration: number;
  fps: number | null;
  items: RundownItem[];
  clips: Clip[];
  t: number;
  onSeek: (t: number) => void;
  onOpen: (clipId: number) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(800);
  const sprite = useQuery({
    queryKey: ["jobs", jobId, "sprite"],
    queryFn: () => api.get<SpriteInfo>(`/media/jobs/${jobId}/sprite.json`),
    staleTime: Infinity,
    retry: false,
  });
  const waveform = useWaveform(jobId);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const c = canvas.current;
    const w = waveform.data;
    if (!c || !w || !duration) return;
    const ratio = window.devicePixelRatio || 1;
    c.width = width * ratio;
    c.height = WAVE_H * ratio;
    const ctx = c.getContext("2d")!;
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, WAVE_H);
    ctx.fillStyle = "#8fb3e8";
    const perPx = (w.peaks.length / duration) * (duration / width);
    for (let px = 0; px < width; px++) {
      const from = Math.floor(px * perPx);
      const to = Math.max(from + 1, Math.floor((px + 1) * perPx));
      let peak = 0;
      for (let i = from; i < to && i < w.peaks.length; i++) peak = Math.max(peak, w.peaks[i]);
      const h = peak * (WAVE_H - 2);
      ctx.fillRect(px, (WAVE_H - h) / 2, 1, h);
    }
  }, [waveform.data, width, duration]);

  if (!duration) return null;
  const x = (sec: number) => (sec / duration) * width;
  const at = (clientX: number) => {
    const r = box.current!.getBoundingClientRect();
    return Math.max(0, Math.min(duration, ((clientX - r.left) / r.width) * duration));
  };
  const clipByItem = new Map(clips.map((c) => [c.rundown_item_id, c]));

  // Thumbnail tiles across the width.
  const tiles = [];
  const s = sprite.data;
  if (s) {
    const tileW = (STRIP_H * s.width) / s.height;
    const n = Math.max(1, Math.floor(width / tileW));
    const scale = STRIP_H / s.height;
    for (let i = 0; i < n; i++) {
      const sec = ((i + 0.5) / n) * duration;
      const idx = Math.min(s.count - 1, Math.floor(sec / s.interval));
      tiles.push(
        <div
          key={i}
          className="tl-tile"
          style={{
            width: width / n,
            backgroundImage: `url(/media/jobs/${jobId}/sprite.jpg)`,
            backgroundSize: `${s.columns * s.width * scale}px ${s.rows * s.height * scale}px`,
            backgroundPosition: `-${(idx % s.columns) * s.width * scale}px -${Math.floor(idx / s.columns) * s.height * scale}px`,
          }}
        />,
      );
    }
  }

  return (
    <div className="job-timeline" ref={box} onClick={(e) => onSeek(at(e.clientX))}>
      <div className="tl-strip" style={{ height: STRIP_H }}>{tiles}</div>
      <canvas ref={canvas} className="tl-wave" style={{ width, height: WAVE_H }} />
      <div className="tl-blocks" style={{ height: BLOCK_H }}>
        {items.map((it) => {
          const clip = clipByItem.get(it.id);
          const start = clip?.start_sec ?? it.aligned_start;
          const end = clip?.end_sec ?? it.aligned_end;
          if (start == null || end == null) return null;
          const warn = clip && (clip.flags.length > 0 || clip.needs_manual_range);
          const cls = clip ? `tl-block clip status-${clip.status}` : "tl-block other";
          const label = clip
            ? `${it.item_no}. ${it.title} — ${CLIP_STATUS_LABEL[clip.status] ?? clip.status}${clip.flags.length ? ` (${clip.flags.map((f) => CLIP_FLAG_LABEL[f] ?? f).join(", ")})` : ""}`
            : `${it.item_no}. ${it.title}`;
          return (
            <div
              key={it.id}
              className={cls}
              style={{ left: x(start), width: Math.max(2, x(end) - x(start)) }}
              title={`${label}\n${timecode(start, fps)} – ${timecode(end, fps)}${clip ? "\n더블클릭: 검수" : ""}`}
              onDoubleClick={(e) => {
                e.stopPropagation();
                if (clip) onOpen(clip.id);
              }}
            >
              <span>{it.item_no}</span>
              {warn && <span className="tl-warn">!</span>}
            </div>
          );
        })}
      </div>
      <div className="tl-playhead" style={{ left: x(t) }} />
    </div>
  );
}
