import type { Clip } from "../api/client";
import { ReadyToPostCard } from "./ReadyToPostCard";

export interface ReadyToPostGridProps {
  groupes: Array<[number, Clip[]]>;
  titreVod: (id: number) => string;
  /** Reglage `captions_emojis` : ce que le rendu incrustera vraiment. */
  emojisActifs: boolean;
  keptIds: Set<number>;
  onKeep: (clip: Clip) => void;
  onDelete: (clip: Clip) => void;
  onEdit: (id: number) => void;
}

/** La grille « Prête à poster », un groupe de cartes par VOD. */
export function ReadyToPostGrid({
  groupes,
  titreVod,
  emojisActifs,
  keptIds,
  onKeep,
  onDelete,
  onEdit,
}: ReadyToPostGridProps) {
  return (
    <>
      {groupes.map(([vodId, lot]) => (
        <section key={vodId} className="space-y-2">
          <h3
            className="truncate text-sm font-semibold text-ink-soft"
            title={titreVod(vodId)}
          >
            {titreVod(vodId)}
            <span className="ml-2 font-normal text-ink-dim">
              {lot.length} clip{lot.length > 1 ? "s" : ""}
            </span>
          </h3>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            {lot.map((c) => (
              <ReadyToPostCard
                key={c.id}
                clip={c}
                emojisActifs={emojisActifs}
                kept={keptIds.has(c.id)}
                onKeep={onKeep}
                onDelete={onDelete}
                onEdit={onEdit}
              />
            ))}
          </div>
        </section>
      ))}
    </>
  );
}
