import type { ReactNode } from "react";

/** count = rempli (emphase forte), signal = teinte + bordure, status = teinte seule. */
export type BadgeVariant = "signal" | "count" | "status";
export type BadgeTone = "mute" | "accent" | "ok" | "danger" | "info";

const FILL: Record<BadgeTone, string> = {
  mute: "border-transparent bg-mute-fill text-on-mute",
  accent: "border-transparent bg-accent text-on-accent",
  ok: "border-transparent bg-ok-fill text-on-ok",
  danger: "border-transparent bg-danger-fill text-on-danger",
  info: "border-transparent bg-info-fill text-on-info",
};

const OUTLINE: Record<BadgeTone, string> = {
  mute: "border-line-control bg-mute-soft text-mute-ink",
  accent: "border-accent bg-accent-soft text-accent-hi",
  ok: "border-ok bg-ok-soft text-ok-ink",
  danger: "border-danger bg-danger-soft text-danger-ink",
  info: "border-info bg-info-soft text-info-ink",
};

const SOFT: Record<BadgeTone, string> = {
  mute: "border-transparent bg-mute-soft text-mute-ink",
  accent: "border-transparent bg-accent-soft text-accent-hi",
  ok: "border-transparent bg-ok-soft text-ok-ink",
  danger: "border-transparent bg-danger-soft text-danger-ink",
  info: "border-transparent bg-info-soft text-info-ink",
};

const DOT: Record<BadgeTone, string> = {
  mute: "bg-mute",
  accent: "bg-accent",
  ok: "bg-ok",
  danger: "bg-danger",
  info: "bg-info",
};

export interface BadgeProps {
  children: ReactNode;
  variant?: BadgeVariant;
  tone?: BadgeTone;
  /** Pastille clignotante : reservee aux etats en cours (analyse, rendu). */
  pulse?: boolean;
  className?: string;
}

export function Badge({
  children,
  variant = "status",
  tone = "mute",
  pulse = false,
  className = "",
}: BadgeProps) {
  const skin =
    variant === "count"
      ? FILL[tone]
      : variant === "signal"
        ? OUTLINE[tone]
        : SOFT[tone];
  const shape =
    variant === "count"
      ? "rounded-md px-1.5 py-0.5 text-xs font-bold tabular-nums"
      : "rounded-full px-2.5 py-0.5 text-xs font-semibold";
  /* La pastille signale un ETAT : elle n'appartient qu'a `status`. `signal`
     se distingue par sa bordure teintee, `count` par son remplissage. */
  const dot = variant === "status";
  return (
    <span
      className={`inline-flex items-center gap-1.5 border align-middle leading-5 ${shape} ${skin} ${className}`}
    >
      {dot && (
        <span
          aria-hidden="true"
          className={`size-1.5 shrink-0 rounded-full ${DOT[tone]} ${pulse ? "animate-pulse" : ""}`}
        />
      )}
      {children}
    </span>
  );
}
