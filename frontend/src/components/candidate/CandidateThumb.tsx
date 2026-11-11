import { useEffect, useState } from "react";
import {
  candidatePreviewUrl,
  candidateThumbUrl,
  type Segment,
} from "../../api/client";
import { clockAt, spokenLength } from "../../lib/time";
import { Thumb } from "../ui/Thumb";

/* L'apercu est un extrait de quelques dizaines de kilooctets produit par le
   pipeline : il demarre en quelques dizaines de ms. Ce delai large ne couvre que
   le cas lent, une VOD analysee avant l'arrivee des apercus, dont l'extrait est
   genere a la demande au premier survol puis mis en cache. L'attente ne masque
   rien : la vignette fixe reste affichee tant que la video n'est pas prete. */
const PREVIEW_TIMEOUT_MS = 12000;

export interface CandidateThumbProps {
  segment: Segment;
  vodId: number;
  rank: number;
  onFocus: (id: number) => void;
}

/** Vignette du candidat, avec lecture silencieuse de 3 s au survol. */
export function CandidateThumb({
  segment,
  vodId,
  rank,
  onFocus,
}: CandidateThumbProps) {
  // L'apercu ne se charge qu'au survol et se coupe en sortant : demonter la
  // balise annule la requete en cours.
  const [apercu, setApercu] = useState(false);
  const [lecture, setLecture] = useState(false);
  const [apercuHS, setApercuHS] = useState(false);

  const quitter = () => {
    setApercu(false);
    setLecture(false);
  };

  useEffect(() => {
    if (!apercu || lecture) return;
    const minuteur = setTimeout(() => {
      setApercuHS(true);
      setApercu(false);
      setLecture(false);
    }, PREVIEW_TIMEOUT_MS);
    return () => clearTimeout(minuteur);
  }, [apercu, lecture]);

  return (
    /* Bouton et non simple survol : le clavier declenche le meme apercu que la
       souris, et le clic designe le candidat sur la timeline. */
    <button
      type="button"
      aria-label={`Aperçu du candidat ${rank} à ${clockAt(segment.start_s)}`}
      onMouseEnter={() => !apercuHS && setApercu(true)}
      onMouseLeave={quitter}
      onFocus={() => {
        onFocus(segment.id);
        if (!apercuHS) setApercu(true);
      }}
      onBlur={quitter}
      onClick={() => onFocus(segment.id)}
      className="block w-full"
    >
      <Thumb
        src={candidateThumbUrl(vodId, segment.start_s)}
        alt=""
        className="w-full rounded-none ring-0"
      >
        {apercu && (
          <video
            key={segment.id}
            muted
            autoPlay
            playsInline
            preload="none"
            tabIndex={-1}
            aria-hidden="true"
            src={candidatePreviewUrl(vodId, segment.start_s)}
            onPlaying={() => setLecture(true)}
            onError={() => {
              setApercuHS(true);
              quitter();
            }}
            className={`absolute inset-0 size-full object-cover transition-opacity duration-150 ease-soft ${lecture ? "opacity-100" : "opacity-0"}`}
          />
        )}
        <span className="absolute right-2 bottom-2 z-10 rounded bg-black/75 px-1.5 py-0.5 font-mono text-[0.6875rem] text-ink">
          {clockAt(segment.start_s)} (
          {spokenLength(segment.end_s - segment.start_s)})
        </span>
        {apercuHS && (
          <span className="absolute bottom-2 left-2 z-10 rounded bg-black/75 px-1.5 py-0.5 text-[0.6875rem] text-ink-dim">
            Aperçu indisponible
          </span>
        )}
      </Thumb>
    </button>
  );
}
