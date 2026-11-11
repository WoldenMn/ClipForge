import { useState } from "react";
import { CandidatesPage } from "./pages/CandidatesPage";
import { EditorPage } from "./pages/EditorPage";
import { ExportPage } from "./pages/ExportPage";
import { HomePage } from "./pages/HomePage";

export type Route =
  | { page: "home" }
  | { page: "candidates"; vodId: number }
  | { page: "editor"; clipId: number }
  | { page: "export" };

const ONGLETS: Array<{ page: Route["page"]; libelle: string }> = [
  { page: "home", libelle: "Accueil" },
  { page: "export", libelle: "Prêts à poster" },
];

/* L'editeur et les candidats appartiennent au parcours d'une VOD : c'est
   « Accueil » qui reste souligne quand on y descend, sinon aucun onglet ne
   l'est et la barre semble avoir perdu sa place. */
const RATTACHEMENT: Record<Route["page"], Route["page"]> = {
  home: "home",
  candidates: "home",
  editor: "home",
  export: "export",
};

export default function App() {
  const [route, setRoute] = useState<Route>({ page: "home" });
  const courant = RATTACHEMENT[route.page];

  return (
    <div className="min-h-screen">
      <nav className="flex items-center gap-1 border-b border-line px-6 py-2.5">
        <span className="mr-5 flex items-center gap-2">
          <img src="/icon.svg" alt="" width={22} height={22} />
          <span className="text-base font-bold tracking-tight text-ink">
            ClipForge
          </span>
        </span>
        {ONGLETS.map(({ page, libelle }) => (
          <button
            key={page}
            type="button"
            aria-current={courant === page ? "page" : undefined}
            onClick={() => setRoute({ page } as Route)}
            className={`rounded-control px-3 py-1.5 text-sm font-semibold transition duration-150 ease-soft active:translate-y-px ${
              courant === page
                ? "bg-accent-soft text-accent-hi"
                : "text-ink-soft hover:bg-surface hover:text-ink"
            }`}
          >
            {libelle}
          </button>
        ))}
      </nav>
      <main className="mx-auto max-w-6xl p-6">
        {route.page === "home" && <HomePage go={setRoute} />}
        {route.page === "candidates" && (
          <CandidatesPage vodId={route.vodId} go={setRoute} />
        )}
        {route.page === "editor" && (
          <EditorPage clipId={route.clipId} go={setRoute} />
        )}
        {route.page === "export" && <ExportPage go={setRoute} />}
      </main>
    </div>
  );
}
