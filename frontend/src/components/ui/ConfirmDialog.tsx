import { type ReactNode, useEffect, useId, useRef } from "react";
import { Button } from "./Button";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
  /** Ce qui va disparaitre, enonce sans ambiguite. */
  children: ReactNode;
}

/** Confirmation d'un geste destructeur, sur le <dialog> natif.
 *
 * showModal() apporte le piege a focus, la couche superieure et Echap sans une
 * ligne de JavaScript. Le premier element focusable est « Annuler » : une Entree
 * reflexe ne peut pas declencher la suppression. */
export function ConfirmDialog({
  open,
  title,
  confirmLabel,
  onConfirm,
  onCancel,
  busy = false,
  children,
}: ConfirmDialogProps) {
  const boite = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const el = boite.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  return (
    <dialog
      ref={boite}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault(); // Echap ne ferme que par notre chemin d'annulation
        if (!busy) onCancel();
      }}
      onMouseDown={(e) => {
        // Le fond appartient au <dialog> lui-meme : une pression dont la cible
        // EST la boite vient donc du fond. Sur mousedown et non sur click, sinon
        // un glisser commence dans le panneau et relache dehors fermerait aussi.
        if (e.target === boite.current && !busy) onCancel();
      }}
      className="m-auto w-full max-w-lg animate-rise rounded-card border border-line-strong bg-surface p-6 text-ink shadow-pop backdrop:bg-black/70"
    >
      <h2 id={titleId} className="text-lg font-semibold text-ink">
        {title}
      </h2>
      <div className="mt-4 space-y-3 text-sm text-ink-soft">{children}</div>
      <div className="mt-6 flex justify-end gap-3">
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          Annuler
        </Button>
        <Button variant="danger" loading={busy} onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </dialog>
  );
}
