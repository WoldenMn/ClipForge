import type { ReactNode } from "react";
import { CHAMP } from "./ui/field";

export interface SubtitleStyleValue {
  font_size: number;
  primary_hex: string;
  highlight_hex: string;
  uppercase: boolean;
}

export interface SubtitleStyleFormProps {
  style: SubtitleStyleValue;
  onChange: (s: SubtitleStyleValue) => void;
  /** Actions du bloc, alignees a droite de la legende (« définir par défaut »). */
  action?: ReactNode;
}

export function SubtitleStyleForm({
  style,
  onChange,
  action,
}: SubtitleStyleFormProps) {
  return (
    <fieldset>
      <legend className="mb-2 flex w-full items-center justify-between gap-3">
        <span className="text-sm font-semibold text-ink">Sous-titres</span>
        {action}
      </legend>
      <div className="flex flex-wrap items-end gap-4 rounded-card border border-line bg-surface p-3 text-sm">
        <label className="text-ink-soft">
          Taille
          <input
            type="number"
            min="1"
            value={style.font_size}
            onChange={(e) =>
              onChange({ ...style, font_size: Number(e.target.value) })
            }
            className={`${CHAMP} mt-1.5 block h-8 w-20 text-sm tabular-nums`}
          />
        </label>
        <label className="text-ink-soft">
          Couleur mot
          <input
            type="color"
            value={style.primary_hex}
            onChange={(e) =>
              onChange({ ...style, primary_hex: e.target.value })
            }
            className="mt-1.5 block h-8 w-14 cursor-pointer rounded-control border border-line-control bg-well p-1"
          />
        </label>
        <label className="text-ink-soft">
          Couleur surbrillance
          <input
            type="color"
            value={style.highlight_hex}
            onChange={(e) =>
              onChange({ ...style, highlight_hex: e.target.value })
            }
            className="mt-1.5 block h-8 w-14 cursor-pointer rounded-control border border-line-control bg-well p-1"
          />
        </label>
        <label className="flex h-8 items-center gap-2 text-ink-soft">
          <input
            type="checkbox"
            checked={style.uppercase}
            onChange={(e) =>
              onChange({ ...style, uppercase: e.target.checked })
            }
            className="size-4 accent-[var(--color-accent)]"
          />
          MAJUSCULES
        </label>
      </div>
    </fieldset>
  );
}
