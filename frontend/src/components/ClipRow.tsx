import { type Clip, candidateThumbUrl, STATUS_FR } from "../api/client";
import { spokenLength } from "../lib/time";
import { Badge, type BadgeTone } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { IconCut, IconRetry } from "./ui/icons";
import { Thumb } from "./ui/Thumb";

const TONS: Record<string, BadgeTone> = {
  draft: "mute",
  queued: "info",
  rendering: "accent",
  done: "ok",
  error: "danger",
};

export interface ClipRowProps {
  clip: Clip;
  onEdit: (id: number) => void;
  onRetry: (id: number) => void;
}

/** Une ligne de la file d'export : ce qu'on rend, ou ca en est, quoi faire. */
export function ClipRow({ clip, onEdit, onRetry }: ClipRowProps) {
  const enCours = clip.status === "rendering" || clip.status === "queued";

  return (
    <Card padding="none" className="animate-rise">
      <div className="flex items-center gap-4 p-3">
        <Thumb
          src={candidateThumbUrl(clip.vod_id, clip.start_s)}
          alt=""
          className="w-28 self-start"
        >
          <span className="absolute right-1 bottom-1 rounded bg-black/75 px-1 py-0.5 text-[0.6875rem] font-semibold tabular-nums text-ink">
            {spokenLength(clip.end_s - clip.start_s)}
          </span>
        </Thumb>

        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex items-start justify-between gap-3">
            {/* `trim` et pas seulement `||` : un titre reduit a des espaces est
                vide pour l'oeil mais vrai pour JavaScript, et la ligne restait
                blanche au lieu de retomber sur le numero du clip. */}
            <p className="min-w-0 truncate font-medium text-ink">
              {clip.title.trim() || `Clip ${clip.id}`}
            </p>
            <Badge tone={TONS[clip.status] ?? "mute"} pulse={enCours}>
              {STATUS_FR[clip.status] ?? clip.status}
            </Badge>
          </div>

          {clip.status === "done" && clip.output_path && (
            <code
              className="truncate font-mono text-xs text-ink-dim"
              title={clip.output_path}
            >
              {clip.output_path}
            </code>
          )}

          {clip.error && (
            <p
              className="line-clamp-2 text-xs text-danger-ink"
              title={clip.error}
            >
              {clip.error}
            </p>
          )}
        </div>

        {clip.status === "draft" && (
          <Button
            size="sm"
            variant="secondary"
            icon={<IconCut />}
            onClick={() => onEdit(clip.id)}
          >
            Éditer
          </Button>
        )}
        {clip.status === "error" && (
          <Button
            size="sm"
            icon={<IconRetry />}
            onClick={() => onRetry(clip.id)}
          >
            Relancer
          </Button>
        )}
      </div>
    </Card>
  );
}
