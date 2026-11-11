import { useEffect, useState } from "react";
import type { Route } from "../App";
import { api, type Segment, type SignalsSample, type Vod } from "../api/client";
import { CandidateCard } from "../components/CandidateCard";
import { Timeline } from "../components/Timeline";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorBanner } from "../components/ui/ErrorBanner";
import { IconBack, IconCut, IconGrid } from "../components/ui/icons";
import { spokenLength } from "../lib/time";

export function CandidatesPage({
  vodId,
  go,
}: {
  vodId: number;
  go: (r: Route) => void;
}) {
  const [vod, setVod] = useState<Vod | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [signals, setSignals] = useState<SignalsSample | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [choisis, setChoisis] = useState<number[]>([]);
  const [actif, setActif] = useState<number | null>(null);
  const [creation, setCreation] = useState(false);

  useEffect(() => {
    const echec = (e: Error) => setError(e.message);
    api.get<Vod>(`/api/vods/${vodId}`).then(setVod).catch(echec);
    api
      .get<Segment[]>(`/api/vods/${vodId}/segments`)
      .then(setSegments)
      .catch(echec);
    api
      .get<SignalsSample>(`/api/vods/${vodId}/signals`)
      .then(setSignals)
      .catch(echec);
  }, [vodId]);

  const basculer = (id: number) =>
    setChoisis((cur) =>
      cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
    );

  const viser = (id: number) => {
    setActif(id);
    document.getElementById(`cand-${id}`)?.scrollIntoView({ block: "nearest" });
  };

  const editer = async (segId: number) => {
    if (creation) return;
    setCreation(true);
    try {
      const clip = await api.post<{ id: number }>("/api/clips", {
        segment_id: segId,
      });
      go({ page: "editor", clipId: clip.id });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCreation(false);
    }
  };

  const creerLot = async () => {
    setCreation(true);
    try {
      // En serie : le backend ecrit dans SQLite, un envoi groupe n'apporterait rien
      for (const segId of choisis) {
        await api.post("/api/clips", { segment_id: segId });
      }
      setChoisis([]);
      go({ page: "export" });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCreation(false);
    }
  };

  const duree = vod?.duration_s ?? signals?.duration_s ?? 0;

  return (
    <div className="space-y-5 pb-24">
      <Button
        variant="ghost"
        size="sm"
        icon={<IconBack />}
        onClick={() => go({ page: "home" })}
      >
        Accueil
      </Button>

      <div>
        <h2
          className="truncate text-xl font-semibold text-ink"
          title={vod?.title}
        >
          {vod?.title ?? "Chargement…"}
        </h2>
        <p className="mt-1 text-sm text-ink-dim">
          {segments.length} candidat{segments.length > 1 ? "s" : ""}
          {duree ? `, ${spokenLength(duree)} de stream` : ""}
        </p>
      </div>

      {vod?.ai_note && <p className="text-sm text-info-ink">{vod.ai_note}</p>}

      {error && (
        <ErrorBanner message={error} onDismiss={() => setError(null)} />
      )}

      {signals && signals.series.length > 0 && (
        <Card className="animate-rise">
          <h3 className="text-sm font-semibold text-ink">Moments forts</h3>
          <Timeline
            duration={duree}
            series={signals.series}
            segments={segments}
            selectedId={actif}
            onSelect={viser}
          />
        </Card>
      )}

      {segments.length === 0 ? (
        <EmptyState
          title="Aucun candidat"
          description="L'analyse n'a retenu aucun moment fort. Relance-la depuis l'accueil après avoir ajusté les poids des signaux."
          icon={<IconGrid className="size-6" />}
          action={
            <Button onClick={() => go({ page: "home" })}>
              Ajuster les réglages
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-2 gap-4 xl:grid-cols-3">
          {segments.map((s, i) => (
            <div key={s.id} id={`cand-${s.id}`} className="h-full">
              <CandidateCard
                segment={s}
                vodId={vodId}
                rank={i + 1}
                selected={choisis.includes(s.id)}
                active={actif === s.id}
                onToggle={basculer}
                onEdit={editer}
                onFocus={setActif}
              />
            </div>
          ))}
        </div>
      )}

      {choisis.length > 0 && (
        <div className="fixed inset-x-0 bottom-6 z-40 flex justify-center px-6">
          <div className="flex animate-rise items-center gap-4 rounded-card border border-line-strong bg-surface-hi px-5 py-3 shadow-pop">
            <span className="text-sm text-ink">
              <span className="font-semibold tabular-nums">
                {choisis.length}
              </span>{" "}
              candidat{choisis.length > 1 ? "s" : ""} sélectionné
              {choisis.length > 1 ? "s" : ""}
            </span>
            <Button size="sm" variant="ghost" onClick={() => setChoisis([])}>
              Tout désélectionner
            </Button>
            <Button
              size="sm"
              icon={<IconCut />}
              loading={creation}
              onClick={creerLot}
            >
              Créer {choisis.length} clip{choisis.length > 1 ? "s" : ""}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
