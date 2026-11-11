/** Horodatage d'un point dans le stream : 2:41:03 au-dela de l'heure, 4:07 sinon. */
export function clockAt(t: number): string {
  const total = Math.max(0, Math.floor(t));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

/** Duree parlee : « 2 h 41 » pour un stream, « 22 s » pour un candidat. */
export function spokenLength(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} s`;
  const m = Math.round(total / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}

/** Temps restant d'une analyse en cours, arrondi a une precision credible. */
export function timeLeft(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds) || seconds <= 0)
    return null;
  if (seconds < 45) return "~30 s restantes";
  const m = Math.round(seconds / 60);
  if (m < 60) return `~${Math.max(1, m)} min restantes`;
  const h = Math.floor(m / 60);
  const reste = m % 60;
  return reste ? `~${h} h ${reste} min restantes` : `~${h} h restantes`;
}
