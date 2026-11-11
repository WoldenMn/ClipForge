import type { Route } from "../App";
import type { Clip } from "../api/client";
import { ReadyToPostGrid } from "../components/ReadyToPostGrid";
import { UserClipsSection } from "../components/UserClipsSection";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorBanner } from "../components/ui/ErrorBanner";
import { IconFolder, IconGrid, IconRetry } from "../components/ui/icons";
import { useExportQueue } from "../hooks/useExportQueue";

/* Groupes dans l'ordre d'apparition : /api/clips rend les plus recents en
   tete, donc la VOD sur laquelle on vient de travailler reste en haut. */
function grouperParVod(clips: Clip[]): Array<[number, Clip[]]> {
  return clips.reduce<Array<[number, Clip[]]>>((acc, c) => {
    const groupe = acc.find(([id]) => id === c.vod_id);
    if (groupe) groupe[1].push(c);
    else acc.push([c.vod_id, [c]]);
    return acc;
  }, []);
}

export function ExportPage({ go }: { go: (r: Route) => void }) {
  const {
    clips,
    vods,
    emojisActifs,
    error,
    setError,
    relancer,
    ouvrirDossier,
    keptIds,
    garder,
    aSupprimer,
    setASupprimer,
    suppression,
    supprimer,
  } = useExportQueue();

  const echecs = clips.filter((c) => c.status === "error");
  const titreVod = (id: number) =>
    vods.find((v) => v.id === id)?.title ?? `VOD ${id}`;
  const editer = (id: number) => go({ page: "editor", clipId: id });

  // Le mode auto est le produit fini : les clips crees a la main gardent leurs
  // lignes existantes, en dessous, plutot que d'etre melanges dans la grille.
  const autoClips = clips.filter((c) => c.origin === "auto");
  const userClips = clips.filter((c) => c.origin !== "auto");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <h2 className="flex items-baseline gap-2 text-lg font-semibold text-ink">
          Prêts à poster
          {clips.length > 0 && (
            <span className="text-sm font-normal text-ink-dim">
              {clips.length}
            </span>
          )}
        </h2>
        <Button
          size="sm"
          variant="secondary"
          icon={<IconFolder />}
          onClick={ouvrirDossier}
        >
          Ouvrir le dossier de sortie
        </Button>
      </div>

      {error && (
        <ErrorBanner message={error} onDismiss={() => setError(null)} />
      )}

      {echecs.length > 1 && (
        <div className="flex animate-rise items-center justify-between gap-4 rounded-card border border-danger/50 bg-danger-soft px-4 py-3 text-sm text-danger-ink">
          <span>
            <span className="font-semibold tabular-nums">{echecs.length}</span>{" "}
            exports en échec.
          </span>
          <Button
            size="sm"
            variant="danger"
            icon={<IconRetry />}
            onClick={() => relancer(echecs.map((c) => c.id))}
          >
            Tout relancer
          </Button>
        </div>
      )}

      {clips.length === 0 ? (
        <EmptyState
          title="Aucun clip en attente"
          description="Colle l'URL d'une VOD Twitch depuis l'accueil, ou ouvre une VOD analysée pour choisir un moment fort : ça arrivera ici."
          icon={<IconGrid className="size-6" />}
          action={
            <Button onClick={() => go({ page: "home" })}>Voir mes VODs</Button>
          }
        />
      ) : (
        <>
          {autoClips.length === 0 ? (
            <p className="text-sm text-ink-dim">
              Aucun clip généré automatiquement pour l'instant.
            </p>
          ) : (
            <ReadyToPostGrid
              groupes={grouperParVod(autoClips)}
              titreVod={titreVod}
              emojisActifs={emojisActifs}
              keptIds={keptIds}
              onKeep={garder}
              onDelete={setASupprimer}
              onEdit={editer}
            />
          )}

          {userClips.length > 0 && (
            <UserClipsSection
              groupes={grouperParVod(userClips)}
              titreVod={titreVod}
              onEdit={editer}
              onRetry={(id) => relancer([id])}
            />
          )}
        </>
      )}

      <ConfirmDialog
        open={aSupprimer !== null}
        title={`Supprimer « ${aSupprimer?.hook || aSupprimer?.title || `Clip ${aSupprimer?.id ?? ""}`} » ?`}
        confirmLabel="Supprimer le clip"
        busy={suppression}
        onCancel={() => setASupprimer(null)}
        onConfirm={supprimer}
      >
        <p>Cette action retire définitivement ce clip de la grille.</p>
        {aSupprimer?.output_path && (
          <div className="rounded-control border border-line bg-well p-3">
            <p className="text-ink">
              Le fichier déjà monté n'est pas supprimé :
            </p>
            <code className="mt-1.5 block break-all font-mono text-xs text-ink-soft">
              {aSupprimer.output_path}
            </code>
          </div>
        )}
      </ConfirmDialog>
    </div>
  );
}
