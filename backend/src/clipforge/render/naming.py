"""Le nom du fichier livre : translitteration, convention par produit, unicite.

Sorti de queue.py, qui orchestre un rendu et n'a pas a porter en plus les regles
de nommage. Ce module ne connait ni ffmpeg ni le montage : il repond a une seule
question, « sous quel chemin ce clip doit-il atterrir ».
"""
import os
import sqlite3
import unicodedata
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

from clipforge import db as dbmod

FILENAME_MAX_CHARS = 60


def safe_filename(text: str, fallback: str) -> str:
    """Titre libre vers nom de fichier Windows sur, jamais vide.

    Trois passes dans cet ordre : les accents tombent par decomposition (« tres »
    et non « très », qu'un titre ecrit par l'IA porte a chaque phrase), la liste
    blanche retire ce que le systeme de fichiers refuse, puis les espaces sont
    recolles — sans quoi le nom garderait les trous de la ponctuation retiree.
    """
    decompose = unicodedata.normalize("NFKD", text or "")
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    retenu = "".join(c for c in sans_accents if c.isalnum() or c in " -_")
    return " ".join(retenu.split())[:FILENAME_MAX_CHARS].strip() or fallback


def output_name(title: str, fallback: str = "clip") -> str:
    """Nom du fichier livre par le mode auto : AAAA-MM-JJ - titre.mp4.

    La date est celle du montage, lue a l'heure LOCALE : c'est le jour du
    calendrier de l'utilisateur qui regroupe ce qu'il va poster, pas celui d'UTC
    — a une heure du matin les deux ne designent plus le meme dossier mental.
    """
    jour = datetime.now(UTC).astimezone()
    return f"{jour:%Y-%m-%d} - {safe_filename(title, fallback)}.mp4"


def rangs(vise: Path) -> Iterator[Path]:
    """Le nom de la convention, puis « (2) », « (3) »... sans fin.

    Cinq clips au meme titre le meme jour visent le meme nom : sans rang le
    dernier rendu ecrase les quatre autres, la base annonce cinq lignes 'done'
    qui pointent toutes sur un seul fichier, et rien nulle part ne dit que
    quatre montages viennent d'etre perdus. Le nom de la spec est une BASE, pas
    une interdiction d'unicite. Le rang se prend a la PROMOTION
    (render.livraison.promouvoir) : pris au demarrage, il restait libre pendant
    tout le rendu, et deux clips homonymes rendus de front visaient le meme
    fichier (revue finale V6, fix round 2, K1).
    """
    yield vise
    for rang in count(2):
        yield vise.with_name(f"{vise.stem} ({rang}){vise.suffix}")


def output_target(conn: sqlite3.Connection, clip: sqlite3.Row, clip_id: int,
                  output_dir: str) -> Path:
    """Nom de BASE du livrable : deux produits, deux conventions.

    Un clip du mode auto est un livrable pret a poster : il porte sa date. Un
    export de l'editeur porte l'id du clip, pour que deux exports du meme
    candidat restent distinguables dans le dossier de sortie. Le rang qui evite
    d'ecraser le fichier d'un autre clip est pris a la promotion.
    """
    defaut = f"clip{clip_id}"
    titre = clip["title"] or ""
    nom = (output_name(titre, defaut) if clip["origin"] == "auto"
           else f"{safe_filename(titre, defaut)} [c{clip_id}].mp4")
    custom_out = dbmod.get_setting(conn, "output_dir")
    out_dir = Path(custom_out) if custom_out else Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / nom


def livrable_du_clip(conn: sqlite3.Connection, clip_id: int,
                     output_path: str | None) -> Path | None:
    """Le livrable precedent de CE clip, s'il est encore a lui seul.

    `clips.output_path` peut etre perime : A livre X.mp4, B « X (2).mp4 »,
    l'utilisateur retire X.mp4 de son dossier, la retouche de B descend sur
    X.mp4. La retouche de A remplacait alors le livrable de B, sous deux lignes
    'done' qui pointaient le meme fichier (revue finale V6, fix round 3, F3).
    Un nom qu'une autre ligne revendique n'est plus le sien : la promotion
    prendra le rang libre suivant.

    Comparaison normalisee, pas un `=` SQL : `output_dir` est un champ texte
    libre, et retaper `c:\\videos` au lieu de `C:\\Videos` suffisait a rendre les
    deux chemins differents pour la base alors qu'ils nomment le meme fichier
    pour Windows -- la garde ne voyait pas le conflit, os.replace le voyait, et
    le montage de l'autre clip etait detruit (re-revue du fix round 3).
    """
    if not output_path:
        return None
    cle = os.path.normcase(os.path.abspath(output_path))
    autre = any(cle == os.path.normcase(os.path.abspath(ligne["output_path"]))
                for ligne in conn.execute(
                    "SELECT output_path FROM clips WHERE id<>? AND output_path IS NOT NULL",
                    (clip_id,)))
    return None if autre else Path(output_path)
