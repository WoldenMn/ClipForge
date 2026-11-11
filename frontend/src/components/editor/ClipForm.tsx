import { useEffect, useRef, useState } from "react";
import { api, type Clip, type Vod } from "../../api/client";
import type { ClipEditor } from "../../hooks/useClipEditor";
import { type Borne, ClipBoundsEditor } from "../ClipBoundsEditor";
import { ProfileSelector } from "../ProfileSelector";
import { SubtitleStyleForm } from "../SubtitleStyleForm";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { CHAMP } from "../ui/field";
import { IconBookmark, IconExport } from "../ui/icons";
import { WordsEditor } from "../WordsEditor";

const CONFIRMATION_MS = 1500;

export interface ClipFormProps {
  editor: ClipEditor;
  /** Deja etroitis par la page : le formulaire ne s'affiche jamais a vide. */
  clip: Clip;
  vod: Vod;
  borneActive: Borne;
  onBorneActive: (b: Borne) => void;
  onNudge: (b: Borne, delta: number) => void;
  exporting: boolean;
  onExport: () => void;
}

/** Panneau de reglage du clip : titre, bornes, cadrage, sous-titres, mots. */
export function ClipForm({
  editor,
  clip,
  vod,
  borneActive,
  onBorneActive,
  onNudge,
  exporting,
  onExport,
}: ClipFormProps) {
  const [prereglage, setPrereglage] = useState(false);
  const minuteur = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (minuteur.current) clearTimeout(minuteur.current);
    },
    [],
  );

  const profile = editor.profiles.find((p) => p.id === clip.profile_id) ?? null;

  /* Le prereglage vit dans les reglages, pas dans le clip : c'est le backend qui
     l'appose sur les clips crees ensuite, sans quoi un clip cree depuis la liste
     puis exporte sans passage par l'editeur ne l'aurait jamais recu. */
  const definirParDefaut = async () => {
    try {
      await api.patch("/api/settings", {
        values: { subtitle_style_default: JSON.stringify(editor.style) },
      });
      setPrereglage(true);
      minuteur.current = setTimeout(
        () => setPrereglage(false),
        CONFIRMATION_MS,
      );
    } catch (e) {
      editor.setError((e as Error).message);
    }
  };

  return (
    <Card className="flex-1 space-y-5">
      <label className="block">
        <span className="text-sm font-semibold text-ink">Titre du clip</span>
        <input
          value={clip.title}
          placeholder="Titre affiché sur la vidéo exportée"
          onChange={(e) => editor.setClip({ ...clip, title: e.target.value })}
          onBlur={() => editor.patch({ title: clip.title })}
          className={`${CHAMP} mt-1.5 h-10 w-full text-[0.9375rem]`}
        />
      </label>

      <ClipBoundsEditor
        startS={clip.start_s}
        endS={clip.end_s}
        active={borneActive}
        onActive={onBorneActive}
        onNudge={onNudge}
        onCommit={editor.commitBounds}
        onError={editor.setError}
      />

      <ProfileSelector
        profiles={editor.profiles}
        selectedId={profile?.id ?? null}
        videoW={vod.width ?? 1920}
        frameUrl={`/api/vods/${clip.vod_id}/frame?t=${clip.start_s.toFixed(1)}`}
        onSelect={(id) => editor.patch({ profile_id: id })}
        onCreate={editor.createProfile}
      />

      <SubtitleStyleForm
        style={editor.style}
        onChange={editor.setStyle}
        action={
          <Button
            size="sm"
            variant="ghost"
            icon={<IconBookmark />}
            onClick={definirParDefaut}
          >
            {prereglage ? "Préréglage enregistré" : "Définir par défaut"}
          </Button>
        }
      />

      <WordsEditor words={editor.words} onChange={editor.setWords} />

      <div className="flex items-center gap-3 border-t border-line pt-4">
        <Button icon={<IconExport />} loading={exporting} onClick={onExport}>
          {exporting ? "Export en cours…" : "Exporter ce clip"}
        </Button>
        <span className="text-xs text-ink-dim">
          ou la touche Entrée, et « ? » pour tous les raccourcis
        </span>
      </div>
    </Card>
  );
}
