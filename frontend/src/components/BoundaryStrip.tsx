import { useRef, useState } from "react";

import type { SceneCut, SilenceRange, Waveform } from "../api/types";
import { snapToCut } from "../labels";

interface Props {
  view: [number, number]; // seconds shown
  inT: number | null;
  outT: number | null;
  playhead: number;
  cuts: SceneCut[];
  silences: SilenceRange[];
  waveform: Waveform | undefined;
  fps: number;
  disabled: boolean;
  onSeek: (t: number) => void;
  onChange: (inT: number, outT: number) => void;
}

const H = 64;

/** The clip and its surroundings: waveform, silences (grey), scene cuts (snap marks), IN/OUT
 * handles (drag; Alt = no snapping) and the playhead (click to seek). */
export function BoundaryStrip({ view, inT, outT, playhead, cuts, silences, waveform, fps, disabled, onSeek, onChange }: Props) {
  const ref = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<{ edge: "in" | "out"; t: number } | null>(null);
  const [v0, v1] = view;
  const span = Math.max(0.001, v1 - v0);
  const x = (t: number) => ((t - v0) / span) * 1000;
  const timeAt = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    return v0 + ((clientX - r.left) / r.width) * span;
  };
  const cutTimes = cuts.map((c) => c.time);
  const shownIn = drag?.edge === "in" ? drag.t : inT;
  const shownOut = drag?.edge === "out" ? drag.t : outT;

  const peaks: string[] = [];
  if (waveform && waveform.peaks.length) {
    const from = Math.max(0, Math.floor(v0 * waveform.rate));
    const to = Math.min(waveform.peaks.length, Math.ceil(v1 * waveform.rate));
    for (let i = from; i < to; i++) {
      const px = x(i / waveform.rate);
      const h = waveform.peaks[i] * (H / 2 - 4);
      peaks.push(`M${px.toFixed(1)} ${H / 2 - h} V${H / 2 + h}`);
    }
  }

  return (
    <svg
      ref={ref}
      className="boundary-strip"
      viewBox={`0 0 1000 ${H}`}
      preserveAspectRatio="none"
      onPointerDown={(e) => {
        if (!(e.target as Element).classList.contains("handle")) onSeek(timeAt(e.clientX));
      }}
      onPointerMove={(e) => {
        if (!drag) return;
        const raw = timeAt(e.clientX);
        setDrag({ ...drag, t: e.altKey ? raw : snapToCut(raw, drag.edge, cutTimes, fps) });
      }}
      onPointerUp={() => {
        if (!drag) return;
        const a = drag.edge === "in" ? drag.t : inT;
        const b = drag.edge === "out" ? drag.t : outT;
        setDrag(null);
        if (a != null && b != null && b - a >= 1) onChange(a, b);
      }}
    >
      {silences
        .filter((s) => s.end > v0 && s.start < v1)
        .map((s) => (
          <rect key={s.start} className="silence" x={x(s.start)} width={x(s.end) - x(s.start)} y={0} height={H} />
        ))}
      {shownIn != null && shownOut != null && (
        <rect className="clip-range" x={x(shownIn)} width={x(shownOut) - x(shownIn)} y={0} height={H} />
      )}
      <path className="wave" d={peaks.join("")} />
      {cuts
        .filter((c) => c.time > v0 && c.time < v1)
        .map((c) => (
          <line key={c.time} className="cut" x1={x(c.time)} x2={x(c.time)} y1={0} y2={H} />
        ))}
      {(["in", "out"] as const).map((edge) => {
        const t = edge === "in" ? shownIn : shownOut;
        if (t == null) return null;
        return (
          <rect
            key={edge}
            className={`handle ${edge}${disabled ? " disabled" : ""}`}
            x={x(t) - (edge === "in" ? 0 : 6)}
            width={6}
            y={0}
            height={H}
            onPointerDown={(e) => {
              if (disabled) return;
              e.stopPropagation();
              (e.currentTarget.ownerSVGElement as SVGSVGElement).setPointerCapture(e.pointerId);
              setDrag({ edge, t });
            }}
          />
        );
      })}
      <line className="playhead" x1={x(playhead)} x2={x(playhead)} y1={0} y2={H} />
    </svg>
  );
}
