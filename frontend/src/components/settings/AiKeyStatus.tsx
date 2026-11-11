import type { EnvStatus } from "../../api/client";

/** Ligne lecture seule : la cle IA gouverne les accroches, jamais le scoring.
 *
 * Rendu conditionne a `env` non nul plutot qu'un statut "indetermine" : un
 * appel /api/settings/env en echec ne doit pas affirmer un etat de la cle
 * qu'il n'a pas pu constater. */
export function AiKeyStatus({ env }: { env: EnvStatus | null }) {
  if (!env) return null;
  const detectee = env.anthropic_key === "presente";
  return (
    <p className="rounded-control border border-line bg-well px-3 py-2 text-xs text-ink-soft">
      <span className="font-semibold text-ink">Accroches IA</span> (titres et
      emojis, indépendantes du scoring ci-dessus) : actives dès qu'une clé est
      détectée : {detectee ? "clé détectée." : "clé absente."}
      {!detectee && (
        <>
          {" "}
          Ajoute <code className="font-mono">ANTHROPIC_API_KEY=ta_clé</code>{" "}
          dans le fichier <code className="font-mono">.env</code> à la racine du
          projet, puis redémarre le serveur.
        </>
      )}
    </p>
  );
}
