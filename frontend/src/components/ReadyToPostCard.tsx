import { type Clip, candidateThumbUrl, STATUS_FR } from "../api/client";
import { spokenLength } from "../lib/time";
import { Badge, type BadgeTone } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { ErrorNote } from "./ui/ErrorNote";
import { IconBookmark, IconCut, IconTrash } from "./ui/icons";
import { Thumb } from "./ui/Thumb";

const TONS: Record<string, BadgeTone> = {
  draft: "mute",
  queued: "info",
  rendering: "accent",
  done: "ok",
  error: "danger",
};

const EN_MONTAGE = new Set(["queued", "rendering"]);

export interface ReadyToPostCardProps {
  clip: Clip;
  /** Reglage `captions_emojis` : ce que le rendu incrustera vraiment. */
  emojisActifs: boolean;
  /** Coche localement, apres un /keep reussi — jamais relu depuis le serveur. */
  kept: boolean;
  onKeep: (clip: Clip) => void;
  onDelete: (clip: Clip) => void;
  onEdit: (id: number) => void;
}

/** Une carte « Prête à poster » : l'accroche d'abord — c'est elle, le produit.
 *
 * Supprimer reste actif pendant le montage sauf pour CE clip : le T8 a montre
 * qu'une suppression pendant un rendu en vol orpheline le fichier de sortie. */
export function ReadyToPostCard({
  clip,
  emojisActifs,
  kept,
  onKeep,
  onDelete,
  onEdit,
}: ReadyToPostCardProps) {
  const enCours = clip.status === "rendering" || clip.status === "queued";
  const montageEnCours = EN_MONTAGE.has(clip.status);

  return (
    <Card padding="none" className="animate-rise h-full">
      <div className="flex h-full items-start gap-4 p-3">
        <Thumb
          src={candidateThumbUrl(clip.vod_id, clip.start_s)}
          alt=""
          className="w-36 self-start"
        >
          <span className="absolute right-1 bottom-1 rounded bg-black/75 px-1 py-0.5 text-[0.6875rem] font-semibold tabular-nums text-ink">
            {spokenLength(clip.end_s - clip.start_s)}
          </span>
        </Thumb>

        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex items-start justify-between gap-3">
            <p className="min-w-0 flex-1 text-base leading-snug font-bold text-ink">
              {/* Le rendu coupe l'emoji quand le reglage est a "0" : l'afficher
                  quand meme promettrait une vignette que la video n'aura pas. */}
              {emojisActifs && clip.emoji && (
                <span aria-hidden="true" className="mr-1.5">
                  {clip.emoji}
                </span>
              )}
              {clip.hook ? `« ${clip.hook} »` : "Accroche en attente"}
            </p>
            <Badge tone={TONS[clip.status] ?? "mute"} pulse={enCours}>
              {STATUS_FR[clip.status] ?? clip.status}
            </Badge>
          </div>

          <p className="truncate text-sm text-ink-soft" title={clip.title}>
            {clip.title.trim() || `Clip ${clip.id}`}
          </p>

          {clip.error && <ErrorNote message={clip.error} />}

          <div className="mt-auto flex items-center gap-2 pt-1">
            <Button
              size="sm"
              variant={kept ? "secondary" : "primary"}
              icon={<IconBookmark />}
              disabled={kept}
              title={kept ? "Compté comme gardé" : undefined}
              onClick={() => onKeep(clip)}
            >
              {kept ? "Gardé" : "Garder"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              icon={<IconCut />}
              onClick={() => onEdit(clip.id)}
            >
              Retoucher
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto hover:text-danger-ink"
              icon={<IconTrash />}
              disabled={montageEnCours}
              title={montageEnCours ? "Montage en cours" : undefined}
              onClick={() => onDelete(clip)}
            >
              Supprimer
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
}
