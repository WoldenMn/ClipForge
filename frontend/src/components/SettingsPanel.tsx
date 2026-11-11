import { useEffect, useState } from "react";
import {
  api,
  type EnvStatus,
  type SceneProfile,
  type Settings,
} from "../api/client";
import { AiKeyStatus } from "./settings/AiKeyStatus";
import { type Field, LigneReglage } from "./settings/SettingsField";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { ErrorBanner } from "./ui/ErrorBanner";

const CONFIRMATION_MS = 1500;

const FIELDS: Field[] = [
  { key: "weight_audio", label: "Poids signal audio", type: "number" },
  { key: "weight_chat", label: "Poids signal chat", type: "number" },
  { key: "weight_speech", label: "Poids densité de parole", type: "number" },
  { key: "ai_enabled", label: "Scoring IA (API Claude)", type: "toggle" },
  { key: "whisper_model", label: "Modèle Whisper", type: "text" },
  {
    key: "output_dir",
    label: "Dossier de sortie (vide = data\\output)",
    type: "text",
  },
];

const AUTO_FIELDS: Field[] = [
  {
    key: "channel_handle",
    label: "Pseudo de la chaîne",
    type: "text",
    placeholder: "twitch.tv/tonpseudo",
    maxLength: 40,
    hint: "Signature apposée sur les clips générés. Vide = pas de signature.",
  },
  {
    key: "captions_emojis",
    label: "Emoji d'accroche dans les sous-titres",
    type: "toggle",
  },
  {
    key: "montage",
    label: "Montage (jump cuts, cold open)",
    type: "toggle",
    hint: "Coupé : rendu continu comme avant, sans coupes ni teaser.",
    defaut: "1",
  },
  {
    key: "cut_style",
    label: "Rythme des coupes",
    type: "select",
    options: [
      { value: "doux", label: "Doux" },
      { value: "standard", label: "Standard" },
      { value: "nerveux", label: "Nerveux" },
    ],
    hint: "Longueur des silences coupés : doux garde les respirations, nerveux coupe tout.",
    defaut: "standard",
  },
  {
    key: "auto_clip_count",
    label: "Nombre de clips générés par VOD",
    type: "number",
    step: "1",
    min: 0,
    max: 10,
  },
];

function champProfilAuto(profils: SceneProfile[], courant: string): Field {
  const options = [
    { value: "", label: "L'unique profil" },
    ...profils.map((p) => ({ value: p.name, label: p.name })),
  ];
  if (courant && !profils.some((p) => p.name === courant)) {
    options.push({ value: courant, label: `${courant} (introuvable)` });
  }
  return {
    key: "auto_profile",
    label: "Profil des clips automatiques",
    type: "select",
    options,
    hint: "Cadre les VOD sans profil propre. Un enregistrement envoyé par le pont OBS garde le sien.",
  };
}

export function SettingsPanel() {
  /* null tant que le GET n'a pas abouti : les champs restent masques plutot
     que de presenter leurs defauts comme des valeurs lues (meme principe
     qu'AiKeyStatus). Un champ desactive afficherait encore ce defaut. */
  const [settings, setSettings] = useState<Settings | null>(null);
  const [saved, setSaved] = useState(false);
  /* Deux facons de n'avoir pas de reglages : pas encore lus, ou pas lisibles.
     Le meme message pour les deux ferait passer une panne pour une attente.
     L'echec de lecture vit a part du bandeau : le fermer prend acte du
     message, il ne remet pas le panneau en attente d'un GET deja termine. */
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [env, setEnv] = useState<EnvStatus | null>(null);
  const [profils, setProfils] = useState<SceneProfile[] | null>(null);

  useEffect(() => {
    api
      .get<Settings>("/api/settings")
      .then(setSettings)
      .catch((e) => {
        setLoadFailed(true);
        setLoadError((e as Error).message);
      });
    // Legere (une seule variable d'environnement lue) : pas besoin d'attendre
    // une ouverture explicite du panneau, deja toujours visible sur l'accueil.
    api
      .get<EnvStatus>("/api/settings/env")
      .then(setEnv)
      .catch(() => {});
    api
      .get<SceneProfile[]>("/api/profiles")
      .then(setProfils)
      .catch(() => {});
  }, []);

  const save = async () => {
    if (settings === null) return;
    try {
      await api.patch<Settings>("/api/settings", { values: settings });
      setSaveError(null);
      setSaved(true);
      setTimeout(() => setSaved(false), CONFIRMATION_MS);
    } catch (e) {
      setSaveError((e as Error).message);
    }
  };

  const set = (key: string, v: string) =>
    setSettings((cur) => (cur === null ? cur : { ...cur, [key]: v }));

  return (
    <Card padding="lg" className="animate-rise space-y-4">
      <h2 className="text-lg font-semibold text-ink">Réglages</h2>
      {loadError && (
        <ErrorBanner message={loadError} onDismiss={() => setLoadError(null)} />
      )}
      {saveError && (
        <ErrorBanner message={saveError} onDismiss={() => setSaveError(null)} />
      )}

      {settings === null ? (
        <p className="text-sm text-ink-dim">
          {loadFailed ? "Réglages non lus." : "Chargement…"}
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-x-8 gap-y-3">
          {FIELDS.map((f) => (
            <LigneReglage
              key={f.key}
              f={f}
              valeur={settings[f.key] ?? f.defaut ?? ""}
              onChange={(v) => set(f.key, v)}
            />
          ))}
        </div>
      )}

      <div className="space-y-3 border-t border-line pt-3">
        <p className="text-sm font-semibold text-ink">Mode automatique</p>
        {settings !== null && (
          <div className="grid grid-cols-2 gap-x-8 gap-y-3">
            {AUTO_FIELDS.map((f) => (
              <LigneReglage
                key={f.key}
                f={f}
                valeur={settings[f.key] ?? f.defaut ?? ""}
                onChange={(v) => set(f.key, v)}
              />
            ))}
            {profils !== null && (
              <LigneReglage
                f={champProfilAuto(profils, settings.auto_profile ?? "")}
                valeur={settings.auto_profile ?? ""}
                onChange={(v) => set("auto_profile", v)}
              />
            )}
          </div>
        )}
        <AiKeyStatus env={env} />
      </div>

      <Button size="sm" onClick={save} disabled={settings === null}>
        {saved ? "Enregistré" : "Enregistrer"}
      </Button>
    </Card>
  );
}
