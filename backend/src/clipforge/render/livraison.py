"""Livraison du rendu : ou ffmpeg ecrit, ou le rendu atterrit, ou part un refus.

Sorti de render/output_check.py (revue finale V6, fix round 2) : output_check
juge un rendu, ce module deplace des fichiers. Ce que Windows fait vraiment,
mesure avant de s'appuyer dessus : os.rename vers une cible existante leve
FileExistsError sans l'ecraser (WinError 183) ; renommer un fichier qu'un autre
process tient ouvert en ecriture, ffmpeg compris, echoue (WinError 32).

Trois regles. Le temporaire d'un clip porte l'id du clip : le pool rend deux
clips de front, deux clips homonymes ne partagent plus un fichier. La promotion
n'ecrase jamais le livrable d'un AUTRE clip. Rien n'est supprime : un rendu
refuse, orphelin, non controle ou pret mais non promu part dans le dossier du
job sous un nom qui dit pourquoi.
"""
import errno
import logging
import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path

from clipforge.render.naming import rangs

logger = logging.getLogger(__name__)

# Tout ce que ce module range dans un dossier de job (job_cache le reconnait).
# refus-* est large a dessein : les revisions precedentes ont pose des
# refus-orphelin.mp4, annonces comme « pas a nous » a chaque demarrage.
MIS_DE_COTE = re.compile(
    r"refus-.+\.mp4|non-controle-(nvenc|cpu)( \(\d+\))?\.mp4"
    r"|(orphelin|pret-non-promu)( \(\d+\))?\.mp4")

# Nom d'attente d'un temporaire renomme sur place avant un deplacement vers un
# autre volume : il reste dans le dossier de sortie si la copie echoue.
ECARTE = ".ecarte"


def part_path(out_dir: Path, clip_id: int) -> Path:
    """Temporaire du rendu de ce clip, dans le dossier de livraison.

    Meme volume que le livrable : la promotion est un renommage atomique.
    Suffixe .mp4 : libavformat deduit le conteneur (piege deja paye, cf.
    CLAUDE.md). L'id du clip et non le titre : deux clips homonymes rendus de
    front ecrivaient le meme .part, et le second deplacait celui du premier.
    """
    return out_dir / f"clipforge-c{clip_id}.part.mp4"


def ecarter(rendu: Path, job_dir: Path, motif: str) -> Path | None:
    """Range `rendu` dans le dossier du job sous `<motif>.mp4`, rang si pris.

    Un renommage, jamais une copie : shutil.move retombait sur une copie
    partielle d'un rendu en cours d'ecriture (mesure). Vers un autre volume,
    renommage sur place d'abord, qui echoue si quelqu'un ecrit encore dedans,
    puis deplacement -- sauf un nom d'attente deja pose, qui saute ce renommage
    parce que son existence est elle-meme la preuve qu'il a reussi une fois.
    Leve OSError : l'appelant dit a l'utilisateur quoi faire.
    """
    if not rendu.exists():
        return None
    job_dir.mkdir(parents=True, exist_ok=True)
    cible = next(c for c in rangs(job_dir / f"{motif}.mp4") if not c.exists())
    try:
        os.rename(rendu, cible)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        libre = _libre(rendu)
        if libre != rendu:
            os.rename(rendu, libre)
        shutil.move(str(libre), str(cible))
    return cible


def _libre(rendu: Path) -> Path:
    """Le nom d'attente de ce temporaire ; il ne s'en empile pas un second."""
    return rendu if rendu.name.endswith(ECARTE) else rendu.with_name(rendu.name + ECARTE)


def mettre_de_cote(rendu: Path, job_dir: Path, motif: str) -> Path | None:
    """ecarter, dont l'echec devient un message qui dit ou est le fichier.

    Le chemin cite est celui ou le fichier est VRAIMENT reste : un deplacement
    vers un autre volume qui echoue (disque plein) le laisse sous son nom
    d'attente. Le conseil suit la cause : fermer un programme ne libere pas un
    disque plein (revue finale V6, fix round 3, F3).
    """
    try:
        return ecarter(rendu, job_dir, motif)
    except OSError as exc:
        reste = next((p for p in (rendu, _libre(rendu)) if p.exists()), rendu)
        quoi = ("fermez le programme qui le tient ouvert puis relancez le rendu"
                if isinstance(exc, PermissionError)
                else "relancez le rendu une fois la cause levee")
        raise RuntimeError(
            f"{rendu.name} n'a pas pu etre mis de cote dans {job_dir} ({exc.strerror}) : il "
            f"reste sous {reste} ; {quoi}") from exc


def ecarter_orphelins(temporaires: Iterable[Path | None], job_dir: Path,
                      clip_id: int) -> None:
    """Temporaires laisses par un rendu interrompu de CE clip : celui du dossier
    de sortie actuel, celui que le verrou a note au rendu precedent, celui du
    dossier ou vivait son livrable precedent (reglage change entre-temps), et
    leur nom d'attente .ecarte (deplacement vers un autre volume reste en plan,
    qui bloquait sinon les refus suivants du clip). Un temporaire qu'on ne peut
    pas renommer est tenu ouvert par un TIERS : depuis le verrou par clip (F1),
    un second rendu du meme clip est refuse bien avant d'arriver ici."""
    anciens = [t for t in temporaires if t is not None]
    for part in dict.fromkeys(p for a in anciens for p in (a, _libre(a))):
        try:
            garde = ecarter(part, job_dir, "orphelin")
        except PermissionError as exc:
            raise RuntimeError(f"{part.name} est tenu ouvert par un autre programme (lecteur "
                               "video, antivirus, client de synchro) : fermez-le puis relancez "
                               "le rendu") from exc
        except OSError as exc:
            raise RuntimeError(f"{part} n'a pas pu etre mis de cote ({exc.strerror})") from exc
        if garde is not None:
            logger.warning("Clip %s : temporaire d'un rendu interrompu (%s) mis de cote : %s",
                           clip_id, part, garde)


def lier_puis_retirer(src: Path, dst: Path) -> None:
    """Renommage sans ecrasement hors Windows : os.rename y ecraserait la cible
    sans rien dire. Un lien dur leve FileExistsError si le nom est pris, puis
    l'ancien nom est retire (les octets restent, sous le nouveau)."""
    os.link(src, dst)
    os.unlink(src)


# Sous Windows, os.rename leve FileExistsError si la cible existe (mesure).
# La CI tourne sous Linux : meme garantie par le lien dur.
renommer_sans_ecraser = os.rename if os.name == "nt" else lier_puis_retirer


def promouvoir(rendu: Path, vise: Path, deja_sien: Path | None) -> Path:
    """Donne au rendu controle son nom de livrable, et rend le nom pris.

    Le livrable precedent de CE clip (retouche) est remplace par os.replace.
    Tout autre nom est pris sans ecrasement : s'il existe, c'est le livrable
    d'un autre clip, le rang suivant est tente.
    """
    for candidat in rangs(vise):
        if candidat == deja_sien:
            os.replace(rendu, candidat)
            return candidat
        try:
            renommer_sans_ecraser(rendu, candidat)
            return candidat
        except FileExistsError:
            continue
    raise AssertionError("rangs() ne s'epuise jamais")


def livrer(rendu: Path, vise: Path, deja_sien: Path | None, job_dir: Path) -> Path:
    """promouvoir ; en cas d'echec, le rendu pret part dans le job, jamais perdu.

    L'ancien livrable n'est jamais reecrit en place : une copie qui echoue a
    mi-chemin detruirait le bon fichier. Cas mesure : un lecteur video qui tient
    le livrable ouvert fait echouer os.replace (WinError 5).
    """
    try:
        return promouvoir(rendu, vise, deja_sien)
    except OSError as exc:
        nom = Path(exc.filename2).name if exc.filename2 else vise.name
        garde = mettre_de_cote(rendu, job_dir, "pret-non-promu")
        cause = (f"{nom} est ouvert dans un autre programme : fermez-le puis relancez le rendu"
                 if isinstance(exc, PermissionError)
                 else f"{nom} n'a pas pu etre ecrit ({exc.strerror})")
        raise RuntimeError(f"Le rendu est pret mais {cause} (rendu mis de cote : {garde})"
                           ) from exc
