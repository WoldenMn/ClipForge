import { useState } from "react";
import { api } from "../api/client";
import { Button } from "./ui/Button";
import { ErrorBanner } from "./ui/ErrorBanner";
import { PathNotice } from "./ui/PathNotice";

interface Rapport {
  contenu: string;
  chemin: string;
}

/** Rapport transmissible, disponible en permanence et pas seulement en cas
 * d'erreur : l'état machine sert autant à comprendre une lenteur qu'une panne.
 *
 * Le fichier est écrit par le backend et son chemin affiché ici, plutôt
 * qu'ouvert dans l'explorateur : ouvrir un dossier depuis le serveur demanderait
 * un endpoint qui exécute une commande système, surface inutile pour un gain
 * nul face à un chemin copiable en un clic. */
export function DiagnosticButton() {
  const [chemin, setChemin] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const [enCours, setEnCours] = useState(false);

  const preparer = async () => {
    setEnCours(true);
    try {
      const rapport = await api.get<Rapport>("/api/diagnostic");
      setChemin(rapport.chemin);
      setErreur(null);
    } catch (e) {
      setErreur((e as Error).message);
      setChemin(null);
    } finally {
      setEnCours(false);
    }
  };

  return (
    <div className="space-y-3">
      <Button variant="ghost" size="sm" loading={enCours} onClick={preparer}>
        Préparer un rapport de diagnostic
      </Button>
      {erreur && (
        <ErrorBanner message={erreur} onDismiss={() => setErreur(null)} />
      )}
      {chemin && (
        <PathNotice
          title="Rapport de diagnostic prêt"
          hint="Fichier texte à joindre à un message : état de la machine, VODs, erreurs, usage récent."
          path={chemin}
          onDismiss={() => setChemin(null)}
        />
      )}
    </div>
  );
}
