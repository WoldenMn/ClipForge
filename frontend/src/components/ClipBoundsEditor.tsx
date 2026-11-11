import { useEffect, useState } from "react";
import { clockAt, spokenLength } from "../lib/time";
import { CHAMP } from "./ui/field";

export type Borne = "start" | "end";

const INVALID_BOUNDS_MSG =
  "Bornes invalides : il faut des nombres, un début positif ou nul et une fin après le début. Valeurs réinitialisées.";

function PasBouton({
  signe,
  borne,
  onNudge,
}: {
  signe: -1 | 1;
  borne: Borne;
  onNudge: (b: Borne, delta: number) => void;
}) {
  return (
    <button
      type="button"
      aria-label={`${signe > 0 ? "Avancer" : "Reculer"} la borne de ${borne === "start" ? "début" : "fin"} de 0,5 seconde`}
      onClick={() => onNudge(borne, signe * 0.5)}
      className="h-8 w-7 rounded-control border border-line-control text-sm font-semibold text-ink-soft transition duration-150 ease-soft hover:border-accent hover:text-accent-hi active:translate-y-px"
    >
      {signe > 0 ? "+" : "−"}
    </button>
  );
}

export interface ClipBoundsEditorProps {
  startS: number;
  endS: number;
  /** Borne que les fleches du clavier deplacent. */
  active: Borne;
  onActive: (b: Borne) => void;
  onNudge: (b: Borne, delta: number) => void;
  onCommit: (start: number, end: number) => Promise<boolean>;
  onError: (message: string) => void;
}

/** Bornes du clip : saisie exacte au clavier, ajustement fin au pas de 0,5 s.
 *
 * La borne « active » est un etat affiche, pas le focus DOM : les fleches du
 * clavier doivent continuer a la deplacer alors que le focus reste sur la page,
 * sans quoi chaque appui exigerait d'abord de revenir dans le champ. */
export function ClipBoundsEditor({
  startS,
  endS,
  active,
  onActive,
  onNudge,
  onCommit,
  onError,
}: ClipBoundsEditorProps) {
  const [bounds, setBounds] = useState({
    start: String(startS),
    end: String(endS),
  });

  // Ne se resynchronise que sur startS/endS (jamais sur tout `clip`) : seul ce
  // composant peut faire varier ces deux props via onCommit, donc pas de risque
  // d'ecraser une frappe en cours suite a un patch sans rapport (titre, profil...).
  useEffect(() => {
    setBounds({ start: String(startS), end: String(endS) });
  }, [startS, endS]);

  const handleBlur = async () => {
    const start = Number(bounds.start);
    const end = Number(bounds.end);
    if (
      !Number.isFinite(start) ||
      !Number.isFinite(end) ||
      start < 0 ||
      end <= start
    ) {
      setBounds({ start: String(startS), end: String(endS) });
      onError(INVALID_BOUNDS_MSG);
      return;
    }
    const ok = await onCommit(start, end);
    if (!ok) setBounds({ start: String(startS), end: String(endS) });
  };

  const champ = (borne: Borne, libelle: string, instant: number) => (
    <div
      className={`flex flex-col gap-1.5 rounded-card border p-3 transition duration-150 ease-soft ${
        active === borne
          ? "border-accent bg-accent-soft"
          : "border-line bg-surface"
      }`}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-semibold text-ink">{libelle}</span>
        <span className="font-mono text-xs tabular-nums text-ink-soft">
          {clockAt(instant)}
        </span>
      </div>
      <div className="flex items-center gap-1.5">
        <PasBouton signe={-1} borne={borne} onNudge={onNudge} />
        <input
          type="number"
          step="0.5"
          min="0"
          aria-label={`${libelle} du clip, en secondes`}
          value={bounds[borne]}
          onFocus={() => onActive(borne)}
          onChange={(e) => setBounds({ ...bounds, [borne]: e.target.value })}
          onBlur={handleBlur}
          className={`${CHAMP} h-8 w-28 text-sm tabular-nums`}
        />
        <PasBouton signe={1} borne={borne} onNudge={onNudge} />
      </div>
    </div>
  );

  return (
    <fieldset>
      <legend className="mb-2 flex w-full items-baseline justify-between gap-3">
        <span className="text-sm font-semibold text-ink">Bornes du clip</span>
        <span className="text-xs text-ink-dim">
          durée {spokenLength(endS - startS)}, les flèches déplacent la borne{" "}
          {active === "start" ? "de début" : "de fin"}
        </span>
      </legend>
      <div className="grid grid-cols-2 gap-3">
        {champ("start", "Début", startS)}
        {champ("end", "Fin", endS)}
      </div>
    </fieldset>
  );
}
