import { useState } from "react";
import { api, type Vod } from "../api/client";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { CHAMP } from "./ui/field";
import { IconFolder, IconPlay } from "./ui/icons";

export interface VodImportBarProps {
  onImported: () => void;
  onError: (message: string) => void;
}

/** Le geste principal de l'accueil : une URL Twitch, un clic, tout s'enchaine.
 *
 * « Générer les clips » chaine les deux appels existants (import puis analyse)
 * plutot que de les exposer comme deux etapes separees — c'est l'ecart entre
 * « coller une URL » et « produire des clips prets a poster » que ce bouton
 * referme. L'import d'un fichier local reste possible mais secondaire : il ne
 * declenche pas l'analyse tout seul, comme avant. */
export function VodImportBar({ onImported, onError }: VodImportBarProps) {
  const [twitchUrl, setTwitchUrl] = useState("");
  const [generating, setGenerating] = useState(false);

  const genererClips = async () => {
    if (!twitchUrl || generating) return;
    setGenerating(true);
    let importee = false;
    try {
      const vod = await api.post<Vod>("/api/vods/import-twitch", {
        url: twitchUrl,
      });
      importee = true;
      setTwitchUrl("");
      await api.post(`/api/vods/${vod.id}/analyze`);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      // Un seul rafraichissement, apres l'appel qui a reellement fini : trop
      // tot (juste apres l'import) il manquerait le passage a 'queued' que
      // /analyze ecrit avant de repondre, et la VOD resterait sans suivi SSE
      // (HomePage n'ouvre l'abonnement que sur un statut deja "actif"). Le
      // garde-fou `importee` evite seulement le refresh quand /import a
      // echoue : aucune VOD n'existe alors, rien a montrer.
      if (importee) onImported();
      setGenerating(false);
    }
  };

  const importLocal = async () => {
    try {
      const { path } = await api.post<{ path: string }>("/api/pick-file");
      // Une URL Twitch saisie a cote rattache le chat a l'enregistrement local
      await api.post("/api/vods/import-local", {
        path,
        twitch_url: twitchUrl || null,
      });
      setTwitchUrl("");
      onImported();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  return (
    <Card className="animate-rise">
      <label htmlFor="twitch-url" className="text-sm font-semibold text-ink">
        Coller l'URL d'une VOD Twitch pour générer ses clips
      </label>
      <div className="mt-2 flex items-end gap-2">
        <input
          id="twitch-url"
          value={twitchUrl}
          onChange={(e) => setTwitchUrl(e.target.value)}
          placeholder="https://www.twitch.tv/videos/…"
          className={`${CHAMP} h-11 flex-1 text-sm`}
        />
        <Button
          icon={<IconPlay />}
          loading={generating}
          disabled={!twitchUrl}
          onClick={genererClips}
        >
          Générer les clips
        </Button>
      </div>
      <p className="mt-1.5 text-xs text-ink-dim">
        Importe la VOD puis lance l'analyse dans la foulée : découpage,
        accroches et montage des meilleurs moments.
      </p>

      <div className="mt-4 flex items-center gap-3 border-t border-line pt-3">
        <span className="text-xs text-ink-dim">
          Ou depuis un enregistrement déjà sur ce PC :
        </span>
        <Button
          size="sm"
          variant="ghost"
          icon={<IconFolder />}
          onClick={importLocal}
        >
          Choisir un fichier
        </Button>
      </div>
    </Card>
  );
}
