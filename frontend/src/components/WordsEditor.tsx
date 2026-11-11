export interface EditableWord {
  start: number;
  end: number;
  text: string;
}

export function WordsEditor({
  words,
  onChange,
}: {
  words: EditableWord[];
  onChange: (w: EditableWord[]) => void;
}) {
  return (
    <fieldset>
      <legend className="mb-2 flex w-full items-baseline justify-between gap-3">
        <span className="text-sm font-semibold text-ink">Transcription</span>
        {words.length > 0 && (
          <span className="text-xs text-ink-dim">
            {words.length} mots, clique sur un mot pour le corriger
          </span>
        )}
      </legend>
      <div className="flex max-h-48 flex-wrap gap-1 overflow-y-auto rounded-card border border-line bg-surface p-3">
        {words.length === 0 && (
          <p className="text-sm text-ink-dim">
            Aucun mot transcrit sur cet intervalle.
          </p>
        )}
        {words.map((w, i) => (
          <input
            // biome-ignore lint/suspicious/noArrayIndexKey: liste de longueur fixe, jamais reordonnee/ajoutee/supprimee — seul le texte est modifie en place (onChange), l'index est donc une cle stable
            key={`${w.start}-${i}`}
            value={w.text}
            aria-label={`Mot ${i + 1} (${w.start.toFixed(1)}s)`}
            onChange={(e) => {
              const next = [...words];
              next[i] = { ...w, text: e.target.value };
              onChange(next);
            }}
            style={{ width: `${Math.max(3, w.text.length + 1)}ch` }}
            className="rounded border border-transparent bg-surface-hi px-1 py-0.5 text-sm text-ink transition-colors duration-150 ease-soft hover:border-line-control focus:bg-well"
          />
        ))}
      </div>
    </fieldset>
  );
}
