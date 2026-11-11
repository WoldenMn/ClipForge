import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Controle {
  cle: string;
  ok: boolean;
  detail: string;
  action: string;
}

/** Le détail seul ne dit pas de quoi il parle : « introuvable » a besoin de son
 * sujet. Une clé inconnue retombe sur elle-même plutôt que sur un vide, pour
 * qu'un contrôle ajouté côté serveur reste lisible sans toucher à l'interface. */
const LIBELLES: Record<string, string> = {
  ffmpeg: "ffmpeg",
  ffmpeg_montage: "Version de ffmpeg",
  disque: "Espace disque",
  gpu: "Carte graphique",
  memoire: "Mémoire disponible",
  schema: "Base de données",
  sources: "Fichiers sources",
  indexation: "Indexation des VOD",
};

/** Ce qui va faire échouer une analyse, avec l'action qui le répare.
 *
 * Tâche de fond : l'interface s'affiche sans attendre cette réponse, la
 * bannière apparaît quand elle arrive. Rien n'est monté en attente ici — pas de
 * squelette, pas de place réservée, donc pas une milliseconde de démarrage en
 * plus quand tout va bien.
 *
 * Une requête en échec ne montre rien et part dans la console : le contrôle de
 * santé n'invente pas une panne quand c'est lui qui n'a pas répondu, et un
 * moteur muet a déjà fait remonter son erreur par la liste des VOD. */
export function HealthBanner() {
  const [alertes, setAlertes] = useState<Controle[]>([]);

  useEffect(() => {
    let monte = true;
    api
      .get<{ controles: Controle[] }>("/api/health-checks")
      .then((reponse) => {
        if (monte) setAlertes(reponse.controles.filter((c) => !c.ok));
      })
      .catch((e) => {
        console.warn("contrôle de santé indisponible :", (e as Error).message);
      });
    return () => {
      monte = false;
    };
  }, []);

  if (alertes.length === 0) return null;

  return (
    <div
      role="status"
      className="animate-rise rounded-card border border-danger/45 bg-danger-soft p-4"
    >
      <p className="text-sm font-semibold text-ink">
        {alertes.length === 1
          ? "Un point à régler avant d'analyser"
          : `${alertes.length} points à régler avant d'analyser`}
      </p>
      <ul className="mt-3 space-y-3">
        {alertes.map((c) => (
          <li key={c.cle}>
            <p className="text-sm text-danger-ink">
              <span className="font-medium text-ink">
                {LIBELLES[c.cle] ?? c.cle}
              </span>{" "}
              : {c.detail}
            </p>
            <p className="mt-1.5 rounded-control border border-line bg-well px-3 py-2 font-mono text-xs break-words text-ink-soft select-all">
              {c.action}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
