import { useEffect, useId, useRef } from "react";
import { Button } from "../ui/Button";

/** Une ligne par raccourci : les touches telles qu'elles sont gravees, puis l'effet. */
const RACCOURCIS: Array<[string[], string]> = [
  [["Espace"], "Lire ou mettre en pause l'aperçu"],
  [["←", "→"], "Déplacer la borne active de 0,5 s"],
  [["Maj", "←", "→"], "Déplacer la borne active de 5 s"],
  [["Tab"], "Passer de la borne de début à celle de fin"],
  [["Entrée"], "Lancer l'export de ce clip"],
  [["Échap"], "Revenir à la liste des candidats"],
  [["?"], "Afficher ce panneau"],
];

function Touche({ children }: { children: string }) {
  return (
    <kbd className="inline-flex h-6 min-w-6 items-center justify-center rounded border border-line-control bg-well px-1.5 font-sans text-xs font-semibold text-ink">
      {children}
    </kbd>
  );
}

export interface HotkeysHelpProps {
  open: boolean;
  onClose: () => void;
}

/** Aide des raccourcis, sur le `<dialog>` natif comme les autres boites de l'app.
 *
 * `showModal()` apporte le piege a focus, la couche superieure et Echap sans une
 * ligne de JavaScript — et tant qu'elle est ouverte, `useHotkeys` se tait. */
export function HotkeysHelp({ open, onClose }: HotkeysHelpProps) {
  const boite = useRef<HTMLDialogElement>(null);
  const titreId = useId();

  useEffect(() => {
    const el = boite.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  return (
    <dialog
      ref={boite}
      aria-labelledby={titreId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onMouseDown={(e) => {
        if (e.target === boite.current) onClose();
      }}
      className="m-auto w-full max-w-md animate-rise rounded-card border border-line-strong bg-surface p-6 text-ink shadow-pop backdrop:bg-black/70"
    >
      <h2 id={titreId} className="text-lg font-semibold text-ink">
        Raccourcis clavier
      </h2>
      <dl className="mt-4 space-y-2.5">
        {RACCOURCIS.map(([touches, effet]) => (
          <div key={effet} className="flex items-baseline gap-3">
            <dt className="flex shrink-0 gap-1">
              {touches.map((t) => (
                <Touche key={t}>{t}</Touche>
              ))}
            </dt>
            <dd className="text-sm text-ink-soft">{effet}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-4 text-xs text-ink-dim">
        Aucun raccourci ne se déclenche pendant que tu écris dans un champ.
      </p>
      <div className="mt-6 flex justify-end">
        <Button onClick={onClose}>Fermer</Button>
      </div>
    </dialog>
  );
}
