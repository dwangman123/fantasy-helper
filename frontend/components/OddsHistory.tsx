"use client";

import { useEffect, useRef, useState } from "react";

import type { OddsPoint } from "@/lib/api";
import { dateTime, pct } from "@/lib/format";

const H = 200;
const M = { top: 12, right: 84, bottom: 28, left: 40 };
const DAY_MS = 86_400_000;
const LABEL_GAP = 16; // the least room between the two end labels

type Props = { points: OddsPoint[]; weekStart: string; days: number; left: string; right: string };

// Both teams' chance to win after each daily ingest, placed in time across the matchup week:
// the left team's line in blue, the right team's in red. The gap between them is a tie.
export function OddsHistory({ points, weekStart, days, left, right }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<number | null>(null);
  // Drawn at the container's real width, so text stays at its stated pixel size.
  const [W, setW] = useState(520);
  const hasPoints = points.length > 0;
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setW(Math.max(Math.round(entry.contentRect.width), 280)));
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasPoints]);
  if (!hasPoints) return <p className="sub">No stored predictions for this matchup; each ingest adds one.</p>;

  const [y0, m0, d0] = weekStart.split("-").map(Number);
  const start = new Date(y0, m0 - 1, d0).getTime();
  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;
  const x = (iso: string) => M.left + Math.min(Math.max((Date.parse(iso) - start) / (days * DAY_MS), 0), 1) * plotW;
  const y = (p: number) => M.top + (1 - p) * plotH;
  const xs = points.map((p) => x(p.created_at));
  const last = points.length - 1;
  const step = Math.ceil(days / Math.max(Math.floor(plotW / 44), 1));
  const lines = [
    { name: left, color: "var(--win)", p: (pt: OddsPoint) => pt.p_win },
    { name: right, color: "var(--loss)", p: (pt: OddsPoint) => pt.p_loss },
  ];
  const path = (p: (pt: OddsPoint) => number) =>
    points.map((pt, i) => `${i ? "L" : "M"}${xs[i].toFixed(1)},${y(p(pt)).toFixed(1)}`).join("");
  // End labels, pushed apart when the two lines finish close together.
  const ends = lines.map((l) => y(l.p(points[last])));
  if (Math.abs(ends[0] - ends[1]) < LABEL_GAP) {
    const mid = (ends[0] + ends[1]) / 2;
    const leftOnTop = ends[0] <= ends[1];
    ends[0] = mid + (leftOnTop ? -1 : 1) * (LABEL_GAP / 2);
    ends[1] = mid + (leftOnTop ? 1 : -1) * (LABEL_GAP / 2);
  }

  const nearest = (clientX: number) => {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box) return null;
    const vx = ((clientX - box.left) / box.width) * W;
    let best = 0;
    xs.forEach((px, i) => {
      if (Math.abs(px - vx) < Math.abs(xs[best] - vx)) best = i;
    });
    return best;
  };
  const shown = active;
  const latest = points[last];

  return (
    <div ref={wrapRef}>
      <div className="legend" style={{ marginBottom: 8 }}>
        {lines.map((l) => (
          <span key={l.name}>
            <span className="key" style={{ background: l.color }} />
            {l.name} wins
          </span>
        ))}
      </div>
      <div className="chart">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`Chance to win after each ingest; latest ${left} ${pct(latest.p_win)}, ${right} ${pct(latest.p_loss)}, tie ${pct(latest.p_tie)}`}
          tabIndex={0}
          onPointerMove={(e) => setActive(nearest(e.clientX))}
          onPointerLeave={() => setActive(null)}
          onFocus={() => setActive(last)}
          onBlur={() => setActive(null)}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") setActive((i) => Math.max((i ?? last) - 1, 0));
            if (e.key === "ArrowRight") setActive((i) => Math.min((i ?? last) + 1, last));
          }}
        >
          {[0, 0.5, 1].map((p) => (
            <g key={p}>
              <line x1={M.left} x2={W - M.right} y1={y(p)} y2={y(p)} stroke={p === 0 ? "var(--axis)" : "var(--grid)"} />
              <text x={M.left - 8} y={y(p) + 4} textAnchor="end" fontSize={12} fill="var(--muted)">
                {pct(p)}
              </text>
            </g>
          ))}
          {Array.from({ length: days }, (_, i) => i)
            .filter((i) => i % step === 0)
            .map((i) => {
              const date = new Date(start + i * DAY_MS);
              return (
                <text
                  key={i}
                  x={M.left + ((i + 0.5) / days) * plotW}
                  y={H - 8}
                  textAnchor="middle"
                  fontSize={12}
                  fill="var(--muted)"
                >
                  {date.toLocaleDateString(undefined, { weekday: "short" })}
                </text>
              );
            })}
          {shown !== null && (
            <line x1={xs[shown]} x2={xs[shown]} y1={M.top} y2={M.top + plotH} stroke="var(--axis)" />
          )}
          {lines.map((l, j) => (
            <g key={l.name}>
              <path d={path(l.p)} fill="none" stroke={l.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {points.map((pt, i) => (
                <circle
                  key={pt.created_at}
                  cx={xs[i]}
                  cy={y(l.p(pt))}
                  r={i === shown || i === last ? 5 : 4}
                  fill={l.color}
                  stroke="var(--surface)"
                  strokeWidth={2}
                />
              ))}
              <text x={xs[last] + 10} y={ends[j] + 4} fontSize={13} fill="var(--ink)">
                <tspan fontWeight={600}>{pct(l.p(latest))}</tspan> {l.name}
              </text>
            </g>
          ))}
        </svg>
        {shown !== null && (
          <div
            className="tooltip"
            style={{
              left: `${(xs[shown] / W) * 100}%`,
              top: `${(Math.min(...lines.map((l) => y(l.p(points[shown])))) / H) * 100}%`,
            }}
          >
            <div className="sub">{dateTime(points[shown].created_at)}</div>
            <div>
              {left} <strong>{pct(points[shown].p_win)}</strong> · tie {pct(points[shown].p_tie)} · {right}{" "}
              <strong>{pct(points[shown].p_loss)}</strong>
            </div>
          </div>
        )}
      </div>
      <details>
        <summary>Table</summary>
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th className="num">{left}</th>
              <th className="num">Tie</th>
              <th className="num">{right}</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr key={p.created_at}>
                <td>{dateTime(p.created_at)}</td>
                <td className="num">{pct(p.p_win)}</td>
                <td className="num">{pct(p.p_tie)}</td>
                <td className="num">{pct(p.p_loss)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
