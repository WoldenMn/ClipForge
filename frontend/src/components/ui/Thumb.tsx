import { type ReactNode, useState } from "react";
import { IconFilm } from "./icons";

export interface ThumbProps {
  /** null quand la VOD n'a pas encore de fichier source ou pas de duree connue. */
  src: string | null;
  alt?: string;
  /** Contenu superpose : rang, duree, case a cocher. */
  children?: ReactNode;
  className?: string;
}

/** Vignette 16:9 avec repli explicite : jamais d'icone d'image cassee.
 *
 * L'endpoint /thumb sert un cache disque, mais il rend 404 tant que ffmpeg n'a
 * pas pu produire l'image (VOD sans fichier, instant hors duree). Ce cas est un
 * etat d'affichage normal, pas une panne : on montre une pellicule barree. */
export function Thumb({ src, alt = "", children, className = "" }: ThumbProps) {
  // On memorise QUELLE source a echoue, pas un booleen : changer de source
  // retente d'elle-meme, sans effet de reinitialisation.
  const [enEchec, setEnEchec] = useState<string | null>(null);
  const shown = src && enEchec !== src;

  return (
    <div
      className={`relative isolate aspect-video shrink-0 overflow-hidden rounded-control bg-well ring-1 ring-line ring-inset ${className}`}
    >
      {shown ? (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
          onError={() => setEnEchec(src)}
          className="size-full animate-rise object-cover"
        />
      ) : (
        /* Une pellicule suffit a dire « pas d'image » ; ecrire la phrase la
           repeterait sur chaque carte d'une grille de vingt. Le titre reste
           lisible a la souris et l'annonce reste faite aux lecteurs d'ecran. */
        <div
          role="img"
          aria-label="Vignette indisponible"
          title="Vignette indisponible"
          className="flex size-full items-center justify-center text-ink-dim"
        >
          <IconFilm className="size-6" />
        </div>
      )}
      {children}
    </div>
  );
}
