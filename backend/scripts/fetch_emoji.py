"""Telecharge les 20 PNG Twemoji 72x72 vendorises, une seule fois en dev.

A lancer via `uv run python scripts/fetch_emoji.py` depuis backend/. Les PNG et
ATTRIBUTION.md produits sont commités ; l'app ne telecharge jamais rien au runtime
(contrainte hors-ligne). Nommage des fichiers : voir codepoint_candidates dans
clipforge.render.sfx, seule source de verite partagee avec emoji_path.
"""
import urllib.error
import urllib.request

from clipforge.render.sfx import EMOJI_DIR, VENDORED_EMOJI, codepoint_candidates, emoji_clusters

REPOS = (
    "https://raw.githubusercontent.com/jdecked/twemoji/main/assets/72x72",
    "https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72",
)
MIN_BYTES = 400  # plus petit fichier reel mesure (26a1.png "eclair" = 446 o) ; une page
# d'erreur GitHub sauvee en .png pese 14 o (mesure) — large marge des deux cotes.
PNG_MAGIC = b"\x89PNG"
ATTRIBUTION = EMOJI_DIR.parent / "ATTRIBUTION.md"


class FetchError(RuntimeError):
    pass


def _download(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return resp.read()
    except urllib.error.URLError:
        return None


def _valid_png(data: bytes | None) -> bool:
    return data is not None and len(data) > MIN_BYTES and data.startswith(PNG_MAGIC)


def fetch_one(char: str) -> tuple[str, int]:
    """Telecharge un emoji : sequence complete d'abord, base en repli, 2 depots."""
    candidates = codepoint_candidates(char)
    for name in candidates:
        for repo in REPOS:
            data = _download(f"{repo}/{name}.png")
            if _valid_png(data):
                (EMOJI_DIR / f"{name}.png").write_bytes(data)
                return name, len(data)
    raise FetchError(
        f"Aucun PNG valide pour {candidates!r} sur {REPOS!r} (verifie la connexion reseau)")


def main() -> None:
    EMOJI_DIR.mkdir(parents=True, exist_ok=True)
    clusters = emoji_clusters(VENDORED_EMOJI)
    if len(clusters) != 20:
        raise FetchError(f"Jeu vendorise attendu a 20 emojis, {len(clusters)} segmentes")
    for char in clusters:
        name, size = fetch_one(char)
        print(f"{name}.png : {size} octets")

    fichiers = sorted(EMOJI_DIR.glob("*.png"))
    if len(fichiers) != 20:
        raise FetchError(f"{len(fichiers)} PNG presents apres telechargement, 20 attendus")
    for f in fichiers:
        data = f.read_bytes()
        if not _valid_png(data):
            raise FetchError(f"{f.name} invalide apres ecriture ({len(data)} octets)")

    ATTRIBUTION.write_text(
        "# Attribution\n\n"
        "Emojis : [Twemoji](https://github.com/jdecked/twemoji) (Twitter, Inc.), "
        "licence [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/).\n",
        encoding="utf-8")
    print(f"ATTRIBUTION.md ecrit ({ATTRIBUTION.stat().st_size} octets)")


if __name__ == "__main__":
    main()
