/** Peau commune des champs de saisie.
 *
 * Un seul endroit pour la frontiere du champ : `line-control` est le seul trait
 * mesure >= 3:1 sur `well` (WCAG 1.4.11), et c'est ce contraste qui dit ou
 * commence la zone editable. La hauteur et la largeur restent au formulaire,
 * elles seules changent d'un ecran a l'autre. */
export const CHAMP =
  "rounded-control border border-line-control bg-well px-3 text-ink transition-colors duration-150 ease-soft placeholder:text-ink-dim hover:border-ink-dim disabled:opacity-45";
