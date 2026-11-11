import { useState } from "react";
import type { Segment, SignalSeries } from "../api/client";
import { clockAt } from "../lib/time";

export const SIGNAL_FR: Record<string, string> = {
  audio: "Pic audio",
  chat: "Chat en feu",
  speech: "Parole dense",
};

const SIGNAL_COLOR: Record<string, string> = {
  audio: "var(--color-accent)",
  chat: "var(--color-info)",
  speech: "var(--color-ok)",
};

/* Repere interne de l'aire : etire en largeur par preserveAspectRatio="none".
   Les traits gardent leur epaisseur grace a vector-effect, et tout ce qui doit
   rester geometriquement juste (marqueurs, curseur) est pose en HTML, pas en SVG. */
const VB_W = 1000;
const VB_H = 100;
const PAS_GRADUATION = [300, 600, 900, 1800, 3600, 7200];

function courbe(scores: (number | null)[]): string {
  const n = scores.length;
  if (n < 2) return "";
  return scores
    .map((v, i) => {
      const x = (i / (n - 1)) * VB_W;
      const y = VB_H - Math.min(1, Math.max(0, v ?? 0)) * (VB_H - 3);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function graduations(duration: number): number[] {
  const pas =
    PAS_GRADUATION.find((p) => duration / p <= 10) ??
    PAS_GRADUATION[PAS_GRADUATION.length - 1];
  const marques: number[] = [];
  for (let t = pas; t < duration; t += pas) marques.push(t);
  return marques;
}

export interface TimelineProps {
  duration: number;
  series: SignalSeries[];
  segments: Segment[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}

export function Timeline({
  duration,
  series,
  segments,
  selectedId,
  onSelect,
}: TimelineProps) {
  const [survol, setSurvol] = useState<number | null>(null);
  if (duration <= 0) return null;

  return (
    <div className="relative">
      {survol !== null && (
        <span
          className="pointer-events-none absolute -top-1 z-20 -translate-x-1/2 rounded bg-surface-hi px-1.5 py-0.5 font-mono text-[0.6875rem] text-ink shadow-lift"
          style={{ left: `${Math.min(96, Math.max(4, survol * 100))}%` }}
        >
          {clockAt(survol * duration)}
        </span>
      )}
      {/* biome-ignore lint/a11y/useSemanticElements: <fieldset> groupe des
          controles de formulaire ; ici ce sont des boutons de navigation, et
          role="group" est la valeur ARIA juste pour un ensemble d'objets lies. */}
      <div
        role="group"
        aria-label="Marqueurs des candidats sur la durée du stream"
        className="relative mt-5 h-28 overflow-hidden rounded-control bg-well ring-1 ring-line ring-inset"
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setSurvol(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)));
        }}
        onMouseLeave={() => setSurvol(null)}
      >
        <svg
          viewBox={`0 0 ${VB_W} ${VB_H}`}
          preserveAspectRatio="none"
          className="absolute inset-0 size-full"
          aria-hidden="true"
        >
          <title>Intensite des signaux sur la duree du stream</title>
          {series.map((s) => {
            const pts = courbe(s.scores);
            if (!pts) return null;
            const teinte = SIGNAL_COLOR[s.kind] ?? "var(--color-mute)";
            return (
              <g key={s.kind}>
                <polygon
                  points={`0,${VB_H} ${pts} ${VB_W},${VB_H}`}
                  fill={teinte}
                  fillOpacity="0.2"
                />
                <polyline
                  points={pts}
                  fill="none"
                  stroke={teinte}
                  strokeOpacity="0.85"
                  strokeWidth="1"
                  vectorEffect="non-scaling-stroke"
                />
              </g>
            );
          })}
        </svg>

        {graduations(duration).map((t) => (
          <span
            key={t}
            aria-hidden="true"
            className="absolute inset-y-0 w-px bg-line-strong/70"
            style={{ left: `${(t / duration) * 100}%` }}
          />
        ))}

        {segments.map((s, i) => {
          const actif = s.id === selectedId;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => onSelect(s.id)}
              aria-pressed={actif}
              aria-label={`Candidat ${i + 1} à ${clockAt(s.start_s)}`}
              title={`#${i + 1} à ${clockAt(s.start_s)}`}
              className="group absolute inset-y-0 z-10 flex w-3 -translate-x-1/2 flex-col items-center"
              style={{ left: `${(s.start_s / duration) * 100}%` }}
            >
              {/* Chapeau + tige : les aires de signaux sont ambre et bleues, un
                  marqueur ambre s'y noierait. L'encre claire ne s'y confond pas. */}
              <span
                className={`h-1 w-3 shrink-0 rounded-b transition duration-150 ease-soft ${
                  actif ? "bg-accent-hi" : "bg-ink-soft group-hover:bg-ink"
                }`}
              />
              <span
                className={`w-[2px] flex-1 transition duration-150 ease-soft ${
                  actif
                    ? "bg-accent-hi shadow-glow"
                    : "bg-ink/35 group-hover:bg-ink/80"
                }`}
              />
            </button>
          );
        })}

        {survol !== null && (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 z-20 w-px bg-ink/60"
            style={{ left: `${survol * 100}%` }}
          />
        )}
      </div>

      <div className="mt-2 flex items-center justify-between text-xs text-ink-dim">
        <div className="flex gap-4">
          {series.map((s) => (
            <span key={s.kind} className="flex items-center gap-1.5">
              <span
                aria-hidden="true"
                className="size-2 rounded-full"
                style={{
                  background: SIGNAL_COLOR[s.kind] ?? "var(--color-mute)",
                }}
              />
              {SIGNAL_FR[s.kind] ?? s.kind}
            </span>
          ))}
        </div>
        <span className="font-mono">{clockAt(duration)}</span>
      </div>
    </div>
  );
}
