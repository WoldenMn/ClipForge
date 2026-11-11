import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type Clip,
  keepClip,
  type Settings,
  type Vod,
} from "../api/client";

const ACTIVE_STATUSES = new Set(["queued", "rendering"]);
const PERIODE_ACTIVE_MS = 2000;
const PERIODE_REPOS_MS = 10000;

/** File d'export : sondage adaptatif, ouverture du dossier au premier succes.
 *
 * Porte aussi le sort des clips auto — garder, supprimer — a cote du reste :
 * les trois s'appuient sur le meme `clips` sonde, et separer « garder » dans
 * un hook a part aurait duplique la meme liste sous deux etats qui divergent. */
export function useExportQueue() {
  const [clips, setClips] = useState<Clip[]>([]);
  const [vods, setVods] = useState<Vod[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Le rendu masque l'emoji quand le reglage est coupe (render/queue.py) : la
  // grille doit lire le MEME reglage, sinon la carte promet un emoji que la
  // video livree ne portera pas. Faux tant que la reponse n'est pas la : mieux
  // vaut le faire apparaitre apres coup que promettre puis se dedire.
  const [emojisActifs, setEmojisActifs] = useState(false);
  const openedFor = useRef<Set<number>>(new Set());
  const knownStatuses = useRef<Map<number, string> | null>(null);

  // "Garde" est un geste de mesure a usage unique (voir /api/clips/{id}/keep) :
  // l'etat coche vit ici, dans la session, jamais releve depuis le serveur qui
  // ne porte aucun drapeau "kept" sur le clip.
  const [keptIds, setKeptIds] = useState<Set<number>>(new Set());
  const [aSupprimer, setASupprimer] = useState<Clip | null>(null);
  const [suppression, setSuppression] = useState(false);

  useEffect(() => {
    api
      .get<Vod[]>("/api/vods")
      .then(setVods)
      .catch((e) => setError((e as Error).message));
    // Silencieux : un reglage illisible ne doit pas couvrir d'une banniere
    // rouge une grille de clips par ailleurs parfaitement utilisable.
    api
      .get<Settings>("/api/settings")
      .then((s) => setEmojisActifs(s.captions_emojis === "1"))
      .catch(() => {});
  }, []);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      if (!live) return;
      try {
        const next = await api.get<Clip[]>("/api/clips");
        if (!live) return; // page quittee pendant la requete : pas d'ouverture post-demontage
        // Au tout premier chargement, un clip deja "done" ne doit pas
        // declencher l'ouverture auto du dossier : seul un PASSAGE a "done"
        // constate entre deux polls ouvre l'explorateur.
        const firstLoad = knownStatuses.current === null;
        for (const c of next) {
          if (c.status !== "done") continue;
          const wasAlreadyDone = firstLoad
            ? true
            : knownStatuses.current?.get(c.id) === "done";
          if (!wasAlreadyDone && !openedFor.current.has(c.id)) {
            api.post("/api/open-output").catch(() => {});
          }
          openedFor.current.add(c.id);
        }
        knownStatuses.current = new Map(next.map((c) => [c.id, c.status]));
        setClips(next);
        setError(null);
        const active = next.some((c) => ACTIVE_STATUSES.has(c.status));
        timer = setTimeout(tick, active ? PERIODE_ACTIVE_MS : PERIODE_REPOS_MS);
      } catch (e) {
        if (!live) return;
        setError((e as Error).message);
        timer = setTimeout(tick, PERIODE_REPOS_MS);
      }
    };

    tick();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, []);

  const relancer = useCallback((ids: number[]) => {
    setClips((prev) =>
      prev.map((p) => (ids.includes(p.id) ? { ...p, status: "queued" } : p)),
    );
    for (const id of ids) {
      api
        .post(`/api/clips/${id}/render`)
        .catch((e) => setError((e as Error).message));
    }
  }, []);

  const ouvrirDossier = useCallback(() => {
    api.post("/api/open-output").catch((e) => setError((e as Error).message));
  }, []);

  // Coche seulement si l'API confirme le geste : un keep qui echoue ne doit
  // pas laisser croire que la mesure a ete comptee.
  const garder = useCallback((clip: Clip) => {
    keepClip(clip.id)
      .then(() => setKeptIds((cur) => new Set(cur).add(clip.id)))
      .catch((e) => setError((e as Error).message));
  }, []);

  const supprimer = useCallback(async () => {
    if (!aSupprimer) return;
    setSuppression(true);
    try {
      await api.del(`/api/clips/${aSupprimer.id}`);
      setClips((cur) => cur.filter((c) => c.id !== aSupprimer.id));
      setASupprimer(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSuppression(false);
    }
  }, [aSupprimer]);

  return {
    clips,
    vods,
    emojisActifs,
    error,
    setError,
    relancer,
    ouvrirDossier,
    keptIds,
    garder,
    aSupprimer,
    setASupprimer,
    suppression,
    supprimer,
  };
}
