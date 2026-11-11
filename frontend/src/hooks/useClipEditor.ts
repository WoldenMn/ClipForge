import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type Clip,
  type SceneProfile,
  type Segment,
  type Vod,
} from "../api/client";
import type { Rect } from "../components/RegionSelector";
import type { SubtitleStyleValue } from "../components/SubtitleStyleForm";
import type { EditableWord } from "../components/WordsEditor";

export const DEFAULT_STYLE: SubtitleStyleValue = {
  font_size: 88,
  primary_hex: "#FFFFFF",
  highlight_hex: "#FFD700",
  uppercase: true,
};

/** Etat complet de l'editeur d'un clip : chargement, patch, voisins.
 *
 * Le chargement est atomique — tout est rassemble avant le moindre setState —
 * pour qu'un changement de clip ne laisse jamais l'ecran dans un etat mixte
 * (nouvelles bornes, anciens mots). Un compteur de generation neutralise les
 * reponses d'un clip qu'on a deja quitte : enchainer « suivant » plus vite que
 * le reseau ne repond ne doit pas faire revenir le clip precedent.
 */
export function useClipEditor(clipId: number) {
  const [clip, setClip] = useState<Clip | null>(null);
  const [vod, setVod] = useState<Vod | null>(null);
  const [profiles, setProfiles] = useState<SceneProfile[]>([]);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [words, setWords] = useState<EditableWord[]>([]);
  const [wordsDirty, setWordsDirty] = useState(false);
  const [style, setStyle] = useState<SubtitleStyleValue>(DEFAULT_STYLE);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);

  const load = useCallback(async () => {
    const mien = ++generation.current;
    setClip(null);
    setVod(null);
    setError(null);
    try {
      const c = await api.get<Clip>(`/api/clips/${clipId}`);
      const [v, ps, segs, w] = await Promise.all([
        api.get<Vod>(`/api/vods/${c.vod_id}`),
        api.get<SceneProfile[]>("/api/profiles"),
        api.get<Segment[]>(`/api/vods/${c.vod_id}/segments`),
        c.words_json
          ? Promise.resolve(JSON.parse(c.words_json) as EditableWord[])
          : api.get<EditableWord[]>(
              `/api/vods/${c.vod_id}/words?start_s=${c.start_s}&end_s=${c.end_s}`,
            ),
      ]);
      if (generation.current !== mien) return;
      setClip(c);
      setVod(v);
      setProfiles(ps);
      setSegments(segs);
      setWords(w);
      setWordsDirty(false);
      setStyle(
        c.subtitle_style
          ? { ...DEFAULT_STYLE, ...JSON.parse(c.subtitle_style) }
          : DEFAULT_STYLE,
      );
    } catch (e) {
      if (generation.current === mien) setError((e as Error).message);
    }
  }, [clipId]);
  useEffect(() => {
    load();
  }, [load]);

  const patch = useCallback(
    async (body: Record<string, unknown>): Promise<boolean> => {
      try {
        setClip(await api.patch<Clip>(`/api/clips/${clipId}`, body));
        setError(null);
        return true;
      } catch (e) {
        setError((e as Error).message);
        return false;
      }
    },
    [clipId],
  );

  const commitBounds = useCallback(
    async (start: number, end: number): Promise<boolean> => {
      const ok = await patch({ start_s: start, end_s: end });
      if (!ok || wordsDirty || !clip) return ok;
      try {
        setWords(
          await api.get<EditableWord[]>(
            `/api/vods/${clip.vod_id}/words?start_s=${start}&end_s=${end}`,
          ),
        );
      } catch (e) {
        setError((e as Error).message);
      }
      return true;
    },
    [patch, wordsDirty, clip],
  );

  const createProfile = useCallback(
    async (name: string, cam: Rect, game: Rect): Promise<boolean> => {
      try {
        const p = await api.post<SceneProfile>("/api/profiles", {
          name,
          cam_x: cam.x,
          cam_y: cam.y,
          cam_w: cam.w,
          cam_h: cam.h,
          game_x: game.x,
          game_y: game.y,
          game_w: game.w,
          game_h: game.h,
        });
        setProfiles(await api.get<SceneProfile[]>("/api/profiles"));
        return await patch({ profile_id: p.id });
      } catch (e) {
        setError((e as Error).message);
        return false;
      }
    },
    [patch],
  );

  /** Clip du candidat voisin dans le classement, cree seulement s'il n'existe pas. */
  const ouvrirVoisin = useCallback(
    async (pas: -1 | 1): Promise<number | null> => {
      const ici = segments.findIndex((s) => s.id === clip?.segment_id);
      const cible = ici < 0 ? undefined : segments[ici + pas];
      if (!cible) return null;
      try {
        const existants = await api.get<Clip[]>("/api/clips");
        const deja = existants.find((c) => c.segment_id === cible.id);
        if (deja) return deja.id;
        return (await api.post<Clip>("/api/clips", { segment_id: cible.id }))
          .id;
      } catch (e) {
        setError((e as Error).message);
        return null;
      }
    },
    [segments, clip],
  );

  const rang = segments.findIndex((s) => s.id === clip?.segment_id);
  return {
    clip,
    vod,
    profiles,
    segments,
    words,
    style,
    error,
    rang,
    total: segments.length,
    setClip,
    setError,
    setStyle,
    setWords: (w: EditableWord[]) => {
      setWordsDirty(true);
      setWords(w);
    },
    patch,
    commitBounds,
    createProfile,
    ouvrirVoisin,
  };
}

export type ClipEditor = ReturnType<typeof useClipEditor>;
