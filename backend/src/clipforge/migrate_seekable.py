"""Migration des VOD deja telechargees : indexation du conteneur et apercus.

Le pipeline indexe desormais les captures Twitch au telechargement, mais les VOD
importees avant restent en MP4 fragmente : leur apercu au survol et leur editeur
de clip ne peuvent pas fonctionner. Cette commande les rattrape.

L'inventaire est le comportement par defaut : reecrire plusieurs gigaoctets se
demande explicitement.

    uv run python -m clipforge.migrate_seekable            # inventaire seul
    uv run python -m clipforge.migrate_seekable --apply    # remuxe et genere
"""
import argparse
import logging
import sys
from pathlib import Path

from clipforge import config
from clipforge import db as dbmod
from clipforge.pipeline import previews, remux, thumbs

logger = logging.getLogger(__name__)


def _sources(conn) -> list[tuple[int, str, Path]]:
    """VOD dont le fichier a ete produit par ClipForge, donc reecrivable."""
    out = []
    downloads = config.DOWNLOADS_DIR.resolve()
    for row in conn.execute("SELECT id, title, source_path FROM vods ORDER BY id"):
        p = Path(row["source_path"] or "")
        if not p.is_file():
            continue
        try:
            if p.resolve().is_relative_to(downloads):
                out.append((row["id"], row["title"] or f"vod{row['id']}", p))
        except OSError:
            continue
    return out


def _taille(octets: float) -> str:
    return f"{octets / 2**30:.1f} Go" if octets >= 2**30 else f"{octets / 2**20:.0f} Mo"


def _instants(conn, vod_id: int) -> list[float]:
    """Instants d'apercu attendus : un par candidat, cale sur la vignette."""
    return [r["start_s"] + thumbs.CANDIDATE_LEAD_S for r in conn.execute(
        "SELECT start_s FROM segments WHERE vod_id=? ORDER BY rank", (vod_id,))]


def _manquants(conn, vod_id: int) -> list[float]:
    return [t for t in _instants(conn, vod_id)
            if not previews.preview_path(config.JOBS_DIR, vod_id, round(t, 1)).is_file()]


def inventaire(conn) -> list[dict]:
    """Ce qu'il y a a faire, sans rien modifier."""
    plan = []
    for vod_id, titre, source in _sources(conn):
        plan.append({
            "id": vod_id,
            "titre": titre,
            "source": source,
            "octets": source.stat().st_size,
            "a_remuxer": remux.is_fragmented(source),
            "apercus_manquants": len(_manquants(conn, vod_id)),
        })
    return plan


def appliquer(conn, plan: list[dict]) -> int:
    """Remuxe puis complete les apercus. Retourne le nombre d'echecs.

    L'ordre compte : sur une source non indexee, chaque extrait coute une dizaine
    de secondes d'ouverture de conteneur, contre une fraction de seconde une fois
    le fichier remuxe. Remuxer d'abord rend la generation des apercus quasi gratuite.
    """
    echecs = 0
    for item in plan:
        vod_id, source = item["id"], item["source"]
        if item["a_remuxer"]:
            print(f"  vod {vod_id} : remux de {_taille(item['octets'])}…", flush=True)
            try:
                remux.ensure_seekable(source, progress_cb=None)
            except remux.RemuxError as exc:
                print(f"  vod {vod_id} : ECHEC du remux ({exc})", file=sys.stderr)
                echecs += 1
                continue
        manquants = _manquants(conn, vod_id)
        if manquants:
            print(f"  vod {vod_id} : {len(manquants)} apercu(s) a generer…", flush=True)
        for t in manquants:
            if previews.try_preview(config.JOBS_DIR, vod_id, source, t) is None:
                echecs += 1
    return echecs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="applique la migration (sinon, inventaire seul)")
    args = parser.parse_args()
    config.setup_logging()
    conn = dbmod.get_db(config.DB_PATH)
    try:
        plan = inventaire(conn)
        if not plan:
            print("Aucune VOD telechargee par ClipForge : rien a migrer.")
            return 0
        a_faire = [p for p in plan if p["a_remuxer"] or p["apercus_manquants"]]
        print(f"{len(plan)} VOD examinee(s), {len(a_faire)} a migrer :")
        for p in plan:
            etat = []
            if p["a_remuxer"]:
                etat.append(f"a remuxer ({_taille(p['octets'])})")
            if p["apercus_manquants"]:
                etat.append(f"{p['apercus_manquants']} apercu(s) manquant(s)")
            print(f"  vod {p['id']} — {p['titre'][:50]} : {', '.join(etat) or 'a jour'}")
        if not a_faire:
            return 0
        if not args.apply:
            print("\nInventaire seul. Relancer avec --apply pour executer.")
            return 0
        besoin = max((p["octets"] for p in a_faire if p["a_remuxer"]), default=0)
        print(f"\nApplication ({_taille(besoin)} d'espace libre necessaire au pic) :")
        echecs = appliquer(conn, a_faire)
        print(f"\nTermine, {echecs} echec(s)." if echecs else "\nTermine.")
        return 1 if echecs else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
