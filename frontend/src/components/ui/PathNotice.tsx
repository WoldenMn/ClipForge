import { useState } from "react";
import { Button } from "./Button";

type Copie = "repos" | "faite" | "refusee";

export interface PathNoticeProps {
  title: string;
  path: string;
  hint?: string;
  onDismiss: () => void;
}

/** Chemin d'un fichier laisse sur le disque, copiable en un clic.
 *
 * Pas de bouton « ouvrir le dossier » : il faudrait un endpoint qui accepte un
 * chemin arbitraire, alors que la VOD vient d'etre supprimee de la base et
 * qu'aucune validation cote serveur ne serait plus possible. */
export function PathNotice({ title, path, hint, onDismiss }: PathNoticeProps) {
  const [copie, setCopie] = useState<Copie>("repos");

  const copier = () => {
    navigator.clipboard.writeText(path).then(
      () => setCopie("faite"),
      () => setCopie("refusee"),
    );
  };

  return (
    <div
      role="status"
      className="animate-rise rounded-card border border-line-strong bg-surface p-4 shadow-card"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">{title}</p>
          {hint && <p className="mt-1 text-xs text-ink-soft">{hint}</p>}
        </div>
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Fermer
        </Button>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-control border border-line bg-well px-3 py-2 font-mono text-xs text-ink-soft select-all">
          {path}
        </code>
        <Button size="sm" variant="secondary" onClick={copier}>
          {copie === "faite" ? "Copié" : "Copier le chemin"}
        </Button>
      </div>
      {copie === "refusee" && (
        <p className="mt-2 text-xs text-danger-ink">
          Copie refusée par le navigateur : sélectionne le chemin ci-dessus.
        </p>
      )}
    </div>
  );
}
