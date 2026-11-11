import { useCallback, useRef, useState } from "react";
import type { Route } from "../App";
import { api } from "../api/client";
import type { Borne } from "../components/ClipBoundsEditor";
import { ClipForm } from "../components/editor/ClipForm";
import { EditorNav } from "../components/editor/EditorNav";
import { HotkeysHelp } from "../components/editor/HotkeysHelp";
import {
  type PreviewHandle,
  PreviewVertical,
} from "../components/PreviewVertical";
import { Button } from "../components/ui/Button";
import { ErrorBanner } from "../components/ui/ErrorBanner";
import { useClipEditor } from "../hooks/useClipEditor";
import { useEditorHotkeys } from "../hooks/useEditorHotkeys";

/** En deca, le rendu produirait un clip inexploitable et le backend refuserait. */
const MIN_CLIP_S = 1;

const arrondi = (v: number) => Math.round(v * 100) / 100;
const entre = (v: number, bas: number, haut: number) =>
  Math.min(Math.max(v, bas), haut);

export function EditorPage({
  clipId,
  go,
}: {
  clipId: number;
  go: (r: Route) => void;
}) {
  const editor = useClipEditor(clipId);
  const apercu = useRef<PreviewHandle>(null);
  const [borneActive, setBorneActive] = useState<Borne>("start");
  const [aide, setAide] = useState(false);
  const [exporting, setExporting] = useState(false);

  const { clip, vod, commitBounds, setError } = editor;

  /* Un deplacement borne : jamais avant le debut du stream, jamais au-dela de sa
     fin, et jamais au point de croiser l'autre borne. Quand la valeur ne bouge
     plus (butee atteinte), aucun aller-retour reseau n'est declenche. */
  const deplacer = useCallback(
    (cible: Borne, delta: number) => {
      if (!clip) return;
      const fin = vod?.duration_s ?? Number.POSITIVE_INFINITY;
      if (cible === "start") {
        const debut = arrondi(
          entre(clip.start_s + delta, 0, clip.end_s - MIN_CLIP_S),
        );
        if (debut !== arrondi(clip.start_s)) {
          void commitBounds(debut, clip.end_s);
        }
        return;
      }
      const bout = arrondi(
        entre(clip.end_s + delta, clip.start_s + MIN_CLIP_S, fin),
      );
      if (bout !== arrondi(clip.end_s)) void commitBounds(clip.start_s, bout);
    },
    [clip, vod, commitBounds],
  );

  const exporter = useCallback(async () => {
    if (!clip || exporting) return;
    setExporting(true);
    try {
      const ok = await editor.patch({
        subtitle_style: JSON.stringify(editor.style),
        words_json: JSON.stringify(editor.words),
        title: clip.title,
      });
      if (!ok) return;
      await api.post(`/api/clips/${clip.id}/render`);
      go({ page: "export" });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setExporting(false);
    }
  }, [clip, exporting, editor, go, setError]);

  const versVoisin = useCallback(
    async (pas: -1 | 1) => {
      const id = await editor.ouvrirVoisin(pas);
      if (id !== null) go({ page: "editor", clipId: id });
    },
    [editor, go],
  );

  const retour = useCallback(() => {
    if (clip) go({ page: "candidates", vodId: clip.vod_id });
  }, [clip, go]);

  useEditorHotkeys({
    borneActive,
    basculerBorne: () =>
      setBorneActive((b) => (b === "start" ? "end" : "start")),
    basculerLecture: () => apercu.current?.basculer(),
    deplacer,
    exporter: () => void exporter(),
    retour,
    ouvrirAide: () => setAide(true),
  });

  if (!clip || !vod) {
    return (
      <div className="space-y-4">
        {editor.error ? (
          <>
            <ErrorBanner
              message={editor.error}
              onDismiss={() => setError(null)}
            />
            <Button variant="secondary" onClick={() => go({ page: "home" })}>
              Retour à l'accueil
            </Button>
          </>
        ) : (
          <p className="text-ink-dim">Chargement…</p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <EditorNav
        rang={editor.rang}
        total={editor.total}
        occupe={exporting}
        onRetour={retour}
        onVoisin={(pas) => void versVoisin(pas)}
        onAide={() => setAide(true)}
      />

      {/* Discret mais present : chaque ecran de l'app porte son titre de niveau
          2, sans quoi la structure du document s'arrete au nom de l'app. */}
      <h2 className="truncate text-sm text-ink-dim" title={vod.title}>
        Éditeur : {vod.title}
      </h2>

      {editor.error && (
        <ErrorBanner message={editor.error} onDismiss={() => setError(null)} />
      )}

      <div className="flex animate-rise gap-6">
        <PreviewVertical
          ref={apercu}
          src={`/api/vods/${clip.vod_id}/file`}
          profile={
            editor.profiles.find((p) => p.id === clip.profile_id) ?? null
          }
          startS={clip.start_s}
          endS={clip.end_s}
          videoW={vod.width ?? 1920}
          videoH={vod.height ?? 1080}
        />
        <ClipForm
          editor={editor}
          clip={clip}
          vod={vod}
          borneActive={borneActive}
          onBorneActive={setBorneActive}
          onNudge={deplacer}
          exporting={exporting}
          onExport={() => void exporter()}
        />
      </div>

      <HotkeysHelp open={aide} onClose={() => setAide(false)} />
    </div>
  );
}
