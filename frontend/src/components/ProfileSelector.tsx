import { useState } from "react";
import type { SceneProfile } from "../api/client";
import { type Rect, RegionSelector } from "./RegionSelector";
import { Button } from "./ui/Button";
import { CHAMP } from "./ui/field";

export function ProfileSelector({
  profiles,
  selectedId,
  frameUrl,
  videoW,
  onSelect,
  onCreate,
}: {
  profiles: SceneProfile[];
  selectedId: number | null;
  frameUrl: string;
  videoW: number;
  onSelect: (id: number) => void;
  onCreate: (name: string, cam: Rect, game: Rect) => Promise<boolean>;
}) {
  const [drawing, setDrawing] = useState(false);
  const [name, setName] = useState("Scene principale");

  const handleDone = async (cam: Rect, game: Rect) => {
    // en cas d'echec, on reste en mode trace : le RegionSelector n'est pas
    // demonte, ses rectangles restent visibles, l'erreur remonte via onCreate
    const ok = await onCreate(name, cam, game);
    if (ok) setDrawing(false);
  };

  return (
    <fieldset className="space-y-3">
      <legend className="mb-2 text-sm font-semibold text-ink">
        Profil de scène
      </legend>
      <div className="flex flex-wrap items-end gap-3 rounded-card border border-line bg-surface p-3">
        <label className="text-sm text-ink-soft">
          Profil appliqué
          <select
            value={selectedId ?? ""}
            onChange={(e) => onSelect(Number(e.target.value))}
            className={`${CHAMP} mt-1.5 block h-8 py-0 text-sm`}
          >
            <option value="" disabled>
              Choisir…
            </option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm text-ink-soft">
          Nom du nouveau profil
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={`${CHAMP} mt-1.5 block h-8 text-sm`}
          />
        </label>
        <Button
          size="sm"
          variant={drawing ? "ghost" : "secondary"}
          onClick={() => setDrawing(!drawing)}
        >
          {drawing ? "Annuler le tracé" : "Nouveau profil (tracer)"}
        </Button>
      </div>
      {drawing && (
        <RegionSelector
          videoW={videoW}
          frameUrl={frameUrl}
          onDone={handleDone}
        />
      )}
    </fieldset>
  );
}
