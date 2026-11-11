import type { Clip } from "../api/client";
import { ClipRow } from "./ClipRow";

export interface UserClipsSectionProps {
  groupes: Array<[number, Clip[]]>;
  titreVod: (id: number) => string;
  onEdit: (id: number) => void;
  onRetry: (id: number) => void;
}

/** Les clips créés à la main : mêmes lignes qu'avant la V5, séparées de la
 * grille auto pour ne pas mélanger deux produits différents. */
export function UserClipsSection({
  groupes,
  titreVod,
  onEdit,
  onRetry,
}: UserClipsSectionProps) {
  return (
    <section className="space-y-3 border-t border-line pt-5">
      <h3 className="text-sm font-semibold text-ink-soft">Mes exports</h3>
      {groupes.map(([vodId, lot]) => (
        <section key={vodId} className="space-y-2">
          <h4
            className="truncate text-sm font-semibold text-ink-soft"
            title={titreVod(vodId)}
          >
            {titreVod(vodId)}
            <span className="ml-2 font-normal text-ink-dim">
              {lot.length} clip{lot.length > 1 ? "s" : ""}
            </span>
          </h4>
          {lot.map((c) => (
            <ClipRow key={c.id} clip={c} onEdit={onEdit} onRetry={onRetry} />
          ))}
        </section>
      ))}
    </section>
  );
}
