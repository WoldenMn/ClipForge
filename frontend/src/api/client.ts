export interface Vod {
  id: number;
  title: string;
  source_path: string;
  twitch_url: string | null;
  twitch_vod_id: string | null;
  duration_s: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  status: string;
  progress: number;
  error: string | null;
  error_detail: string | null;
  step_label: string | null;
  eta_s: number | null;
  ai_note: string | null;
  created_at: string;
  profile_id: number | null;
}
export interface DeletedVod {
  deleted: boolean;
  /** Chemin de la video source, JAMAIS supprimee par l'API. */
  source_path: string;
  source_size_mo: number | null;
}
export interface SignalSeries {
  kind: string;
  /** Une valeur par tranche reguliere de step_s ; null = aucune mesure. */
  scores: (number | null)[];
}
export interface SignalsSample {
  duration_s: number;
  step_s: number;
  series: SignalSeries[];
}
export interface Segment {
  id: number;
  vod_id: number;
  start_s: number;
  end_s: number;
  score: number;
  ai_score: number | null;
  ai_title: string | null;
  ai_hashtags: string | null;
  rank: number | null;
  top_signal: string | null;
  signals_count: number | null;
  hook_text: string | null;
}
export interface Clip {
  id: number;
  segment_id: number | null;
  vod_id: number;
  start_s: number;
  end_s: number;
  title: string;
  profile_id: number | null;
  subtitle_style: string | null;
  words_json: string | null;
  status: string;
  progress: number;
  output_path: string | null;
  error: string | null;
  /** 'auto' = propose par la generation automatique, 'user' = cree a la main. */
  origin: string;
  /** Accroche ecrite par l'IA (ou son repli local) pour un clip 'auto'. */
  hook: string | null;
  /** Un seul emoji d'accroche, incruste au rendu si les emojis sont actives. */
  emoji: string | null;
}
export interface SceneProfile {
  id: number;
  name: string;
  cam_x: number;
  cam_y: number;
  cam_w: number;
  cam_h: number;
  game_x: number;
  game_y: number;
  game_w: number;
  game_h: number;
}
export type Settings = Record<string, string>;
/** Presence de la cle IA sur le poste qui fait tourner le backend, jamais sa
    valeur : voir GET /api/settings/env. */
export interface EnvStatus {
  anthropic_key: "presente" | "absente";
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) {
    const detail = await r
      .json()
      .then((d) => d.detail)
      .catch(() => r.statusText);
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  return r.json() as Promise<T>;
}

export const api = {
  get: <T>(url: string) => req<T>(url),
  post: <T>(url: string, body?: unknown) =>
    req<T>(url, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  patch: <T>(url: string, body: unknown) =>
    req<T>(url, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T>(url: string) => req<T>(url, { method: "DELETE" }),
};

/* Ces deux constantes doublent celles du pipeline (pipeline/thumbs.py) : c'est
   ce qui fait tomber l'URL sur une vignette DEJA en cache. Un instant different
   serait valide mais paierait 8 s de decodage par image. */
const VOD_THUMB_RATIO = 0.1;
const CANDIDATE_LEAD_S = 1.0;

/** Vignette servie du cache disque. /frame decode a la volee : ne pas l'utiliser ici. */
export function thumbUrl(vodId: number, t: number): string {
  return `/api/vods/${vodId}/thumb?t=${t.toFixed(1)}`;
}

export function vodThumbUrl(vod: Vod): string | null {
  if (!vod.duration_s || !vod.source_path) return null;
  return thumbUrl(vod.id, vod.duration_s * VOD_THUMB_RATIO);
}

export function candidateThumbUrl(vodId: number, startS: number): string {
  return thumbUrl(vodId, startS + CANDIDATE_LEAD_S);
}

/** Extrait servi du cache disque, au meme instant que la vignette du candidat.
    Jamais la VOD complete : une capture Twitch est un MP4 fragmente sans index,
    que le navigateur ne sait pas seeker sans en telecharger des gigaoctets. */
export function candidatePreviewUrl(vodId: number, startS: number): string {
  const t = startS + CANDIDATE_LEAD_S;
  return `/api/vods/${vodId}/preview?t=${t.toFixed(1)}`;
}

export function subscribeVod(
  vodId: number,
  onUpdate: (v: Vod) => void,
): () => void {
  const es = new EventSource(`/api/vods/${vodId}/events`);
  es.addEventListener("progress", (e) => {
    const v = JSON.parse((e as MessageEvent).data) as Vod;
    onUpdate(v);
    if (v.status === "ready" || v.status === "error") es.close();
  });
  return () => es.close();
}

/** Marque un clip auto comme garde : le geste qui compte pour la mesure V4.
    Idempotent cote serveur ; l'appelant decide seul de l'etat "garde" affiche. */
export function keepClip(clipId: number): Promise<Clip> {
  return api.post<Clip>(`/api/clips/${clipId}/keep`);
}

export const STATUS_FR: Record<string, string> = {
  imported: "Importée",
  downloading: "Téléchargement",
  remuxing: "Indexation vidéo",
  probing: "Lecture métadonnées",
  extracting_audio: "Extraction audio",
  transcribing: "Transcription",
  analyzing: "Analyse audio et chat",
  segmenting: "Découpage",
  ai_scoring: "Scoring IA",
  making_clips: "Fabrication des clips",
  writing_hooks: "Écriture des accroches",
  rendering_clips: "Montage final",
  ready: "Prête",
  error: "Erreur",
  draft: "Brouillon",
  queued: "En file",
  rendering: "Rendu en cours",
  done: "Exporté",
};
