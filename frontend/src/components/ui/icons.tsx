import type { ReactNode } from "react";

export interface IconProps {
  className?: string;
}

/* Jeu d'icones local : l'application tourne hors ligne, aucune police ni
   bibliotheque d'icones distante n'est chargeable. */
function Glyph({
  children,
  className = "size-4",
}: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`${className} shrink-0`}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export const IconFolder = ({ className }: IconProps) => (
  <Glyph className={className}>
    <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.5h7A1.5 1.5 0 0 1 19 10v7.5a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 3 17.5z" />
  </Glyph>
);

export const IconLink = ({ className }: IconProps) => (
  <Glyph className={className}>
    <path d="M10 13.5a3.5 3.5 0 0 0 5 0l3-3a3.54 3.54 0 0 0-5-5l-1.2 1.2" />
    <path d="M14 10.5a3.5 3.5 0 0 0-5 0l-3 3a3.54 3.54 0 0 0 5 5l1.2-1.2" />
  </Glyph>
);

export const IconPlay = ({ className }: IconProps) => (
  <Glyph className={className}>
    <path d="M8 5.6v12.8L19 12z" />
  </Glyph>
);

export const IconRetry = ({ className }: IconProps) => (
  <Glyph className={className}>
    <path d="M20 12a8 8 0 1 1-2.4-5.7" />
    <path d="M20 4.5V9h-4.5" />
  </Glyph>
);

export const IconGrid = ({ className }: IconProps) => (
  <Glyph className={className}>
    <rect x="4" y="4.5" width="7" height="7" rx="1.4" />
    <rect x="13" y="4.5" width="7" height="7" rx="1.4" />
    <rect x="4" y="13" width="7" height="6.5" rx="1.4" />
    <rect x="13" y="13" width="7" height="6.5" rx="1.4" />
  </Glyph>
);

export const IconTrash = ({ className }: IconProps) => (
  <Glyph className={className}>
    <path d="M4.5 7h15M9.5 7V5.6A1.6 1.6 0 0 1 11.1 4h1.8a1.6 1.6 0 0 1 1.6 1.6V7" />
    <path d="M6.6 7l.75 11.6A1.9 1.9 0 0 0 9.25 20.4h5.5a1.9 1.9 0 0 0 1.9-1.8L17.4 7" />
    <path d="M10.3 11v5.6M13.7 11v5.6" />
  </Glyph>
);

export const IconBack = ({ className }: IconProps) => (
  <Glyph className={className}>
    <path d="M14.5 5.5 8 12l6.5 6.5" />
  </Glyph>
);

export const IconCut = ({ className }: IconProps) => (
  <Glyph className={className}>
    <circle cx="6.5" cy="17.5" r="2.5" />
    <circle cx="17.5" cy="17.5" r="2.5" />
    <path d="M8.3 15.7 17 4M15.7 15.7 7 4" />
  </Glyph>
);

export const IconNext = ({ className }: IconProps) => (
  <Glyph className={className}>
    <path d="M9.5 5.5 16 12l-6.5 6.5" />
  </Glyph>
);

export const IconHelp = ({ className }: IconProps) => (
  <Glyph className={className}>
    <circle cx="12" cy="12" r="9" />
    <path d="M9.6 9.4a2.5 2.5 0 1 1 3.3 2.4c-.6.2-.9.8-.9 1.4v.6M12 16.6v.1" />
  </Glyph>
);

export const IconExport = ({ className }: IconProps) => (
  <Glyph className={className}>
    <path d="M12 15.5V4m0 0L8.2 7.8M12 4l3.8 3.8" />
    <path d="M4.5 14v4.5A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5V14" />
  </Glyph>
);

export const IconBookmark = ({ className }: IconProps) => (
  <Glyph className={className}>
    <path d="M6.5 4.8h11a.8.8 0 0 1 .8.8v13.6L12 15.6 5.7 19.2V5.6a.8.8 0 0 1 .8-.8z" />
  </Glyph>
);

export const IconFilm = ({ className }: IconProps) => (
  <Glyph className={className}>
    <rect x="2.8" y="5" width="18.4" height="14" rx="2" />
    <path d="M7.2 5v14M16.8 5v14M2.8 12h18.4" />
  </Glyph>
);
