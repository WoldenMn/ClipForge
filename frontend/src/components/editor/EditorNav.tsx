import { Button } from "../ui/Button";
import { IconBack, IconHelp, IconNext } from "../ui/icons";

export interface EditorNavProps {
  /** Rang du candidat courant dans le classement, -1 si le clip n'en vient pas. */
  rang: number;
  total: number;
  occupe: boolean;
  onRetour: () => void;
  onVoisin: (pas: -1 | 1) => void;
  onAide: () => void;
}

/** Barre de tete de l'editeur : sortie, deplacement dans le classement, aide.
 *
 * Un clip cree a la main (sans candidat d'origine) n'a pas de voisin : le
 * compteur disparait et les deux fleches restent inactives plutot que de
 * pretendre naviguer dans un classement auquel ce clip n'appartient pas. */
export function EditorNav({
  rang,
  total,
  occupe,
  onRetour,
  onVoisin,
  onAide,
}: EditorNavProps) {
  const classe = rang >= 0;
  return (
    <div className="flex items-center gap-2">
      <Button variant="ghost" size="sm" icon={<IconBack />} onClick={onRetour}>
        Candidats
      </Button>

      <div className="ml-auto flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          icon={<IconBack />}
          disabled={!classe || rang === 0 || occupe}
          aria-label="Candidat précédent"
          onClick={() => onVoisin(-1)}
        >
          Précédent
        </Button>
        {classe && (
          <span className="px-1 text-sm tabular-nums text-ink-dim">
            {rang + 1} / {total}
          </span>
        )}
        <Button
          variant="ghost"
          size="sm"
          disabled={!classe || rang === total - 1 || occupe}
          aria-label="Candidat suivant"
          onClick={() => onVoisin(1)}
        >
          Suivant
          <IconNext />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          icon={<IconHelp />}
          aria-label="Raccourcis clavier"
          onClick={onAide}
        >
          Raccourcis
        </Button>
      </div>
    </div>
  );
}
