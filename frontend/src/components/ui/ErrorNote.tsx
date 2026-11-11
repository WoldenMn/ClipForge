import { useId, useState } from "react";

function AlertIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      className="mt-0.5 size-4 shrink-0"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5.5M12 16.4v.1" />
    </svg>
  );
}

export interface ErrorNoteProps {
  message: string;
  detail?: string | null;
}

/** Erreur a deux niveaux : une phrase lisible, la trace derriere « detail ».
 *
 * Une base ecrite avant ce contrat peut encore porter sa trace dans `message`
 * (la migration v5 la deplace, mais un enregistrement en vol peut y echapper) :
 * on ne garde alors que la premiere ligne et on renvoie le reste au detail.
 * Cette premiere ligne est elle-meme bornee a deux lignes d'affichage. */
export function ErrorNote({ message, detail }: ErrorNoteProps) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  const [head, ...tail] = message.split("\n");
  const technique = [tail.join("\n").trim(), detail?.trim()]
    .filter(Boolean)
    .join("\n\n");

  return (
    <div className="rounded-control border border-danger/45 bg-danger-soft px-3 py-2 text-danger-ink">
      <div className="flex items-start gap-2">
        <AlertIcon />
        <p className="line-clamp-2 min-w-0 flex-1 text-sm" title={head}>
          {head}
        </p>
        {technique && (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 rounded border border-danger/50 px-2 py-0.5 text-xs font-semibold transition duration-150 ease-soft hover:bg-danger-fill hover:text-on-danger"
          >
            {open ? "Masquer" : "Détail"}
          </button>
        )}
      </div>
      {open && technique && (
        <pre
          id={panelId}
          className="mt-2 max-h-52 animate-rise overflow-auto rounded border border-line bg-well p-2.5 font-mono text-[0.6875rem] leading-relaxed whitespace-pre-wrap text-ink-soft"
        >
          {technique}
        </pre>
      )}
    </div>
  );
}
