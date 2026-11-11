import { CHAMP } from "../ui/field";

export interface Field {
  key: string;
  label: string;
  type: "number" | "toggle" | "text" | "select";
  placeholder?: string;
  maxLength?: number;
  min?: number;
  max?: number;
  step?: string;
  hint?: string;
  options?: { value: string; label: string }[];
  /** Valeur appliquee si les reglages stockes n'ont pas cette cle. */
  defaut?: string;
}

function ChampReglage({
  f,
  valeur,
  onChange,
}: {
  f: Field;
  valeur: string;
  onChange: (v: string) => void;
}) {
  if (f.type === "toggle") {
    return (
      <input
        id={f.key}
        type="checkbox"
        checked={valeur === "1"}
        onChange={(e) => onChange(e.target.checked ? "1" : "0")}
        className="size-4 shrink-0 accent-[var(--color-accent)]"
      />
    );
  }
  if (f.type === "select") {
    return (
      <select
        id={f.key}
        value={valeur || f.options?.[0]?.value || ""}
        onChange={(e) => onChange(e.target.value)}
        className={`${CHAMP} h-8 w-36 shrink-0 text-sm`}
      >
        {f.options?.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  if (f.type === "number") {
    return (
      <input
        id={f.key}
        type="number"
        step={f.step ?? "0.1"}
        min={f.min}
        max={f.max}
        value={valeur}
        onChange={(e) => onChange(e.target.value)}
        className={`${CHAMP} h-8 w-36 shrink-0 text-sm`}
      />
    );
  }
  return (
    <input
      id={f.key}
      type="text"
      placeholder={f.placeholder}
      maxLength={f.maxLength}
      value={valeur}
      onChange={(e) => onChange(e.target.value)}
      className={`${CHAMP} h-8 w-36 shrink-0 text-sm`}
    />
  );
}

/** Une ligne de réglage : libellé + champ, avec son indice sous le champ si
 * le réglage en porte un (ex. "vide = pas de signature"). */
export function LigneReglage({
  f,
  valeur,
  onChange,
}: {
  f: Field;
  valeur: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label
        htmlFor={f.key}
        className="flex items-center justify-between gap-3 text-sm"
      >
        <span className="text-ink-soft">{f.label}</span>
        <ChampReglage f={f} valeur={valeur} onChange={onChange} />
      </label>
      {f.hint && <p className="text-xs text-ink-dim">{f.hint}</p>}
    </div>
  );
}
