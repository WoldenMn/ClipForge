import { Button } from "./Button";

export interface ErrorBannerProps {
  message: string;
  onDismiss: () => void;
}

/** Erreur d'action, en tete d'ecran et refermable.
 *
 * A distinguer de `ErrorNote`, qui porte l'erreur PERSISTANTE d'une VOD (avec
 * sa trace technique depliable) : ici l'erreur appartient au geste qui vient
 * d'echouer, elle disparait des que l'utilisateur en prend acte. */
export function ErrorBanner({ message, onDismiss }: ErrorBannerProps) {
  return (
    <div
      role="alert"
      className="flex animate-rise items-center justify-between gap-4 rounded-card border border-danger/50 bg-danger-soft px-4 py-3 text-sm text-danger-ink"
    >
      <span className="min-w-0 flex-1">{message}</span>
      <Button size="sm" variant="ghost" onClick={onDismiss}>
        Fermer
      </Button>
    </div>
  );
}
