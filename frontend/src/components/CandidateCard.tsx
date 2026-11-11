import type { Segment } from "../api/client";
import { CandidateThumb } from "./candidate/CandidateThumb";
import { SIGNAL_FR } from "./Timeline";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { IconCut } from "./ui/icons";

const TITLE_MAX_LEN = 60;

function ecourter(titre: string): string {
  return titre.length > TITLE_MAX_LEN
    ? `${titre.slice(0, TITLE_MAX_LEN)}…`
    : titre;
}

export interface CandidateCardProps {
  segment: Segment;
  vodId: number;
  rank: number;
  selected: boolean;
  active: boolean;
  onToggle: (id: number) => void;
  onEdit: (id: number) => void;
  onFocus: (id: number) => void;
}

export function CandidateCard({
  segment,
  vodId,
  rank,
  selected,
  active,
  onToggle,
  onEdit,
  onFocus,
}: CandidateCardProps) {
  const hashtags = segment.ai_hashtags
    ? (JSON.parse(segment.ai_hashtags) as string[])
    : null;
  const titre = segment.ai_title ? ecourter(segment.ai_title) : null;

  return (
    <Card
      padding="none"
      className={`relative flex h-full animate-rise flex-col overflow-hidden ${active ? "ring-2 ring-accent" : ""}`}
    >
      <CandidateThumb
        segment={segment}
        vodId={vodId}
        rank={rank}
        onFocus={onFocus}
      />

      {/* Hors du bouton d'apercu : un controle interactif ne s'imbrique pas. */}
      <label className="absolute top-2 left-2 z-20 flex cursor-pointer items-center gap-1.5 rounded bg-black/75 px-2 py-1 text-xs font-semibold text-ink">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggle(segment.id)}
          className="size-3.5 accent-[var(--color-accent)]"
        />
        #{rank}
      </label>

      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          {segment.top_signal && (
            <Badge variant="signal" tone="accent">
              {SIGNAL_FR[segment.top_signal] ?? segment.top_signal}
            </Badge>
          )}
          {(segment.signals_count ?? 0) >= 2 && (
            <Badge variant="count" tone="mute">
              {segment.signals_count} signaux
            </Badge>
          )}
          <span className="ml-auto text-xs text-ink-soft">
            score{" "}
            <span className="font-semibold tabular-nums text-ink">
              {(segment.ai_score ?? segment.score * 100).toFixed(0)}
            </span>
            {segment.ai_score !== null && " (IA)"}
          </span>
        </div>

        {/* Le scoring IA est optionnel, donc l'absence de titre est la normale,
            pas un incident : c'est alors l'accroche — ce qui se dit vraiment
            dans le clip — qui prend la ligne principale. Ecrire « sans titre »
            vingt fois ne dirait rien de plus que le reglage deja connu. */}
        {titre ? (
          <>
            <p className="font-medium text-ink">{titre}</p>
            {segment.hook_text && (
              <p className="line-clamp-2 text-sm text-ink-soft italic">
                « {segment.hook_text} »
              </p>
            )}
          </>
        ) : (
          segment.hook_text && (
            <p className="line-clamp-3 font-medium text-ink italic">
              « {segment.hook_text} »
            </p>
          )
        )}

        {hashtags && (
          <p className="truncate text-sm text-info-ink">{hashtags.join(" ")}</p>
        )}

        <Button
          size="sm"
          variant="secondary"
          icon={<IconCut />}
          className="mt-auto w-full"
          onClick={() => onEdit(segment.id)}
        >
          Éditer ce clip
        </Button>
      </div>
    </Card>
  );
}
