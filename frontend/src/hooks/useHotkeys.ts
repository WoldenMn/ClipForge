import { useEffect, useRef } from "react";

/** Carte `touche -> action`, indexee sur `KeyboardEvent.key` (« ArrowLeft », « ? », « » pour l'espace). */
export type HotkeyMap = Record<string, (e: KeyboardEvent) => void>;

const CHAMPS_DE_SAISIE = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function vientDUneSaisie(cible: EventTarget | null): boolean {
  if (!(cible instanceof HTMLElement)) return false;
  return CHAMPS_DE_SAISIE.has(cible.tagName) || cible.isContentEditable;
}

/** Raccourcis clavier de l'ecran courant, actifs le temps du montage.
 *
 * Trois refus systematiques, dans cet ordre :
 * 1. une frappe combinee a Ctrl / Alt / Windows — ce sont les raccourcis du
 *    systeme et du navigateur, jamais les notres (Maj reste autorise : « ? »
 *    est une touche Maj sur un clavier francais) ;
 * 2. une frappe venue d'un champ de saisie — le titre du clip contient des
 *    espaces, et un espace ne doit pas mettre la video en pause ;
 * 3. une frappe pendant qu'un `<dialog>` modal est ouvert — la boite possede
 *    ses propres touches (Echap la ferme) et reste seule maitresse du clavier.
 *
 * La repetition automatique (touche maintenue) est ignoree par defaut : sans
 * cela, garder l'espace enfonce basculerait lecture/pause dix fois par seconde.
 *
 * Aucun `preventDefault` implicite : chaque action decide, car une touche comme
 * Tab doit garder son role natif des que le focus est pose sur un controle.
 */
export function useHotkeys(
  carte: HotkeyMap,
  repetables: readonly string[] = [],
): void {
  const dernier = useRef({ carte, repetables });
  useEffect(() => {
    dernier.current = { carte, repetables };
  });

  useEffect(() => {
    const auClavier = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.altKey || e.metaKey) return;
      if (vientDUneSaisie(e.target)) return;
      if (document.querySelector("dialog[open]")) return;
      const { carte: actuelle, repetables: tenues } = dernier.current;
      if (e.repeat && !tenues.includes(e.key)) return;
      actuelle[e.key]?.(e);
    };
    window.addEventListener("keydown", auClavier);
    return () => window.removeEventListener("keydown", auClavier);
  }, []);
}
