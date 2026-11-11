import { useCallback, useEffect, useState } from "react";
import type { Route } from "../App";
import { api, type DeletedVod, subscribeVod, type Vod } from "../api/client";
import { DiagnosticButton } from "../components/DiagnosticButton";
import { HealthBanner } from "../components/HealthBanner";
import { SettingsPanel } from "../components/SettingsPanel";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorBanner } from "../components/ui/ErrorBanner";
import { IconFolder } from "../components/ui/icons";
import { PathNotice } from "../components/ui/PathNotice";
import { VodCard } from "../components/VodCard";
import { VodImportBar } from "../components/VodImportBar";

const REPOS = new Set(["imported", "ready", "error"]);

export function HomePage({ go }: { go: (r: Route) => void }) {
  const [vods, setVods] = useState<Vod[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [aSupprimer, setASupprimer] = useState<Vod | null>(null);
  const [suppression, setSuppression] = useState(false);
  const [restant, setRestant] = useState<DeletedVod | null>(null);

  const refresh = useCallback(() => {
    api
      .get<Vod[]>("/api/vods")
      .then(setVods)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(refresh, [refresh]);

  const activeVodsKey = vods
    .filter((v) => !REPOS.has(v.status))
    .map((v) => v.id)
    .join(",");

  useEffect(() => {
    const activeIds = activeVodsKey ? activeVodsKey.split(",").map(Number) : [];
    const unsubs = activeIds.map((id) =>
      subscribeVod(id, (nv) =>
        setVods((cur) => cur.map((x) => (x.id === nv.id ? nv : x))),
      ),
    );
    return () => {
      for (const u of unsubs) u();
    };
  }, [activeVodsKey]);

  const analyze = (id: number) =>
    api
      .post(`/api/vods/${id}/analyze`)
      .then(refresh)
      .catch((e) => setError((e as Error).message));

  const supprimer = async () => {
    if (!aSupprimer) return;
    setSuppression(true);
    try {
      const res = await api.del<DeletedVod>(`/api/vods/${aSupprimer.id}`);
      setVods((cur) => cur.filter((v) => v.id !== aSupprimer.id));
      setRestant(res.source_path ? res : null);
      setASupprimer(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSuppression(false);
    }
  };

  return (
    <div className="space-y-6">
      <HealthBanner />

      <VodImportBar onImported={refresh} onError={setError} />

      {error && (
        <ErrorBanner message={error} onDismiss={() => setError(null)} />
      )}

      {restant && (
        <PathNotice
          title="VOD supprimée : le fichier vidéo est toujours sur le disque"
          hint={
            restant.source_size_mo
              ? `${restant.source_size_mo} Mo à supprimer toi-même si tu n'en veux plus.`
              : "À supprimer toi-même si tu n'en veux plus."
          }
          path={restant.source_path}
          onDismiss={() => setRestant(null)}
        />
      )}

      <section className="space-y-3">
        <h2 className="flex items-baseline gap-2 text-lg font-semibold text-ink">
          VODs
          {vods.length > 0 && (
            <span className="text-sm font-normal text-ink-dim">
              {vods.length}
            </span>
          )}
        </h2>
        {vods.length === 0 ? (
          <EmptyState
            title="Aucune VOD importée"
            description="Choisis un enregistrement local ou colle l'URL d'une VOD Twitch pour lancer une première analyse."
            icon={<IconFolder className="size-6" />}
          />
        ) : (
          vods.map((v) => (
            <VodCard
              key={v.id}
              vod={v}
              onAnalyze={analyze}
              onOpen={(id) => go({ page: "candidates", vodId: id })}
              onDelete={setASupprimer}
            />
          ))
        )}
      </section>

      <SettingsPanel />

      <DiagnosticButton />

      <ConfirmDialog
        open={aSupprimer !== null}
        title={`Supprimer « ${aSupprimer?.title ?? ""} » ?`}
        confirmLabel="Supprimer la VOD"
        busy={suppression}
        onCancel={() => setASupprimer(null)}
        onConfirm={supprimer}
      >
        <p>Cette action retire définitivement :</p>
        <ul className="list-disc space-y-1 pl-5 text-ink">
          <li>la VOD et toute son analyse (transcription, signaux) ;</li>
          <li>ses clips candidats et les clips créés depuis eux ;</li>
          <li>
            ses fichiers de travail : audio extrait, vignettes, export du chat.
          </li>
        </ul>
        <div className="rounded-control border border-line bg-well p-3">
          <p className="text-ink">Le fichier vidéo source n'est pas touché :</p>
          <code className="mt-1.5 block break-all font-mono text-xs text-ink-soft">
            {aSupprimer?.source_path || "aucun fichier local"}
          </code>
        </div>
      </ConfirmDialog>
    </div>
  );
}
