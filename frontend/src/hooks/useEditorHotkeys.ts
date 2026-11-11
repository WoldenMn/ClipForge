import type { Borne } from "../components/ClipBoundsEditor";
import { useHotkeys } from "./useHotkeys";

/** Pas fin, et pas long quand Maj accompagne la fleche. */
const PAS_S = 0.5;
const PAS_LONG_S = 5;

export interface EditorHotkeysActions {
  borneActive: Borne;
  basculerBorne: () => void;
  basculerLecture: () => void;
  deplacer: (borne: Borne, delta: number) => void;
  exporter: () => void;
  retour: () => void;
  ouvrirAide: () => void;
}

/** Le contrat clavier de l'editeur, en un seul endroit lisible.
 *
 * Chaque action neutralise le comportement natif de sa touche (l'espace fait
 * defiler la page, Entree soumet), a une exception : Tab n'est intercepte que
 * si le focus n'est sur aucun controle. Voler Tab en permanence enfermerait le
 * clavier sur le premier bouton atteint — un piege au sens WCAG 2.1.2, et un
 * ecran inutilisable sans souris. */
export function useEditorHotkeys(a: EditorHotkeysActions): void {
  useHotkeys({
    " ": (e) => {
      e.preventDefault();
      a.basculerLecture();
    },
    ArrowLeft: (e) => {
      e.preventDefault();
      a.deplacer(a.borneActive, e.shiftKey ? -PAS_LONG_S : -PAS_S);
    },
    ArrowRight: (e) => {
      e.preventDefault();
      a.deplacer(a.borneActive, e.shiftKey ? PAS_LONG_S : PAS_S);
    },
    Tab: (e) => {
      if (document.activeElement && document.activeElement !== document.body) {
        return;
      }
      e.preventDefault();
      a.basculerBorne();
    },
    Enter: (e) => {
      e.preventDefault();
      a.exporter();
    },
    Escape: (e) => {
      e.preventDefault();
      a.retour();
    },
    "?": (e) => {
      e.preventDefault();
      a.ouvrirAide();
    },
  });
}
