import { STATUS_FR, type Vod, vodThumbUrl } from "../api/client";
import { spokenLength, timeLeft } from "../lib/time";
import { Badge, type BadgeTone } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { ErrorNote } from "./ui/ErrorNote";
import { IconGrid, IconPlay, IconRetry, IconTrash } from "./ui/icons";
import { Thumb } from "./ui/Thumb";

const REPOS = new Set(["imported", "ready", "error"]);

function tone(status: string): BadgeTone {
  if (status === "ready") return "ok";
  if (status === "error") return "danger";
  if (status === "imported") return "mute";
  return "accent";
}

function ProgressBar({ value, label }: { value: number; label: string }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <span className="truncate text-ink-soft">{label}</span>
        <span className="shrink-0 font-semibold tabular-nums text-accent-hi">
          {value.toFixed(0)} %
        </span>
      </div>
      <div
        role="progressbar"
        aria-valuenow={Math.round(value)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
        className="h-1.5 overflow-hidden rounded-full bg-well ring-1 ring-line ring-inset"
      >
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-150 ease-soft"
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      </div>
    </div>
  );
}

export interface VodCardProps {
  vod: Vod;
  onAnalyze: (id: number) => void;
  onOpen: (id: number) => void;
  onDelete: (vod: Vod) => void;
}

export function VodCard({ vod, onAnalyze, onOpen, onDelete }: VodCardProps) {
  const running = !REPOS.has(vod.status);
  const etape = vod.step_label ?? STATUS_FR[vod.status] ?? vod.status;
  const reste = timeLeft(vod.eta_s);
  const technique = [
    vod.width && vod.height ? `${vod.width}×${vod.height}` : null,
    vod.fps ? `${Math.round(vod.fps)} i/s` : null,
    vod.twitch_vod_id ? `Twitch ${vod.twitch_vod_id}` : null,
  ].filter(Boolean);

  return (
    <Card padding="none" className="animate-rise overflow-hidden">
      <div className="flex gap-4 p-4">
        <Thumb
          src={vodThumbUrl(vod)}
          alt=""
          className="w-44 self-start shadow-card"
        >
          {vod.duration_s ? (
            <span className="absolute right-1.5 bottom-1.5 rounded bg-black/75 px-1.5 py-0.5 text-[0.6875rem] font-semibold tabular-nums text-ink">
              {spokenLength(vod.duration_s)}
            </span>
          ) : null}
        </Thumb>

        <div className="flex min-w-0 flex-1 flex-col gap-2.5">
          <div className="flex items-start justify-between gap-3">
            <h3
              className="min-w-0 truncate text-base font-semibold text-ink"
              title={vod.title}
            >
              {vod.title}
            </h3>
            <Badge tone={tone(vod.status)} pulse={running}>
              {STATUS_FR[vod.status] ?? vod.status}
            </Badge>
          </div>

          {/* Tant que la sonde n'a pas tourne, cette ligne n'a rien a dire :
              elle s'efface au lieu d'annoncer trois inconnues. */}
          {technique.length > 0 && (
            <p className="text-xs text-ink-dim">{technique.join(", ")}</p>
          )}

          {running && (
            <ProgressBar
              value={vod.progress}
              label={reste ? `${etape}, ${reste}` : etape}
            />
          )}

          {vod.status === "error" && vod.error && (
            <ErrorNote message={vod.error} detail={vod.error_detail} />
          )}

          {vod.ai_note && (
            <p className="text-xs text-info-ink">{vod.ai_note}</p>
          )}

          <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
            {vod.status === "imported" && (
              <Button
                size="sm"
                icon={<IconPlay />}
                onClick={() => onAnalyze(vod.id)}
              >
                Analyser
              </Button>
            )}
            {vod.status === "error" && (
              <Button
                size="sm"
                icon={<IconRetry />}
                onClick={() => onAnalyze(vod.id)}
              >
                Reprendre l'analyse
              </Button>
            )}
            {vod.status === "ready" && (
              <Button
                size="sm"
                variant="secondary"
                icon={<IconGrid />}
                onClick={() => onOpen(vod.id)}
              >
                Voir les candidats
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto hover:text-danger-ink"
              icon={<IconTrash />}
              aria-label={`Supprimer ${vod.title}`}
              onClick={() => onDelete(vod)}
            >
              Supprimer
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
}
