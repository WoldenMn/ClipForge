"""Un seul rendu a la fois par clip, quel que soit le process qui le lance.

Le pool de l'API et le pipeline d'analyse rendent chacun dans leur process :
app.state.rendering ne voit pas le second, et le statut en base se lit puis
s'ecrit en deux temps. Deux ffmpeg sur le meme temporaire livraient le clip
deux fois, ou un fichier corrompu marque 'done' (revue finale V6, fix round 3,
F1). Le verrou est pose par le systeme sur jobs/clipN/.verrou (msvcrt sous
Windows, flock ailleurs, ou tourne la CI) : il tombe avec le process qui le
tient, meme tue. Le fichier garde en plus le chemin du temporaire que ce rendu
ecrit : un rendu interrompu se retrouve alors meme si le dossier de sortie a
change depuis (F3).
"""
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

NOM = ".verrou"
# Borne de lecture, en octets : un chemin Windows tient sous 32 767 caracteres,
# donc sous 64 Kio en UTF-8. Les deux lecteurs l'appliquent, sinon un fichier
# gonfle par un tiers serait lu en entier par l'un et tronque par l'autre.
_NOTE_MAX = 1 << 16


class RenduDejaEnCours(RuntimeError):
    """Un autre process rend deja ce clip. Rien n'a ete ecrit."""


if os.name == "nt":
    import msvcrt

    # Un octet loin du debut : msvcrt interdit la zone verrouillee aux autres
    # handles, le contenu du fichier doit rester lisible.
    _OCTET = 1 << 30

    def _prendre(fd: int) -> None:
        os.lseek(fd, _OCTET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _rendre(fd: int) -> None:
        os.lseek(fd, _OCTET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _prendre(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _rendre(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class Verrou:
    """Le verrou tenu, et la note du temporaire qu'il protege.

    La note est lue et ecrite par le detenteur, sur son propre descripteur : le
    verrou porte sur un octet loin du debut, le contenu reste donc lisible par
    un tiers (le rangement du demarrage) sans prendre le verrou.
    """

    def __init__(self, fd: int):
        self._fd = fd

    def temporaire_precedent(self) -> Path | None:
        os.lseek(self._fd, 0, os.SEEK_SET)
        return _chemin(os.read(self._fd, _NOTE_MAX))

    def noter(self, temporaire: Path) -> None:
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, str(temporaire).encode("utf-8"))


def temporaire_note(job_dir: Path) -> Path | None:
    """Le temporaire note par le dernier rendu de ce clip, sans prendre le verrou."""
    try:
        with open(job_dir / NOM, "rb") as note:
            return _chemin(note.read(_NOTE_MAX))
    except OSError:
        return None


def _chemin(brut: bytes) -> Path | None:
    texte = brut.decode("utf-8", errors="replace").strip()
    return Path(texte) if texte else None


@contextmanager
def verrou_du_clip(job_dir: Path) -> Iterator[Verrou]:
    """Tient le verrou du clip le temps du bloc ; RenduDejaEnCours s'il est pris.

    Pris : msvcrt leve PermissionError (EACCES), flock BlockingIOError. Toute
    autre panne remonte telle quelle, elle ne dit rien d'un autre rendu.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(job_dir / NOM, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
    try:
        try:
            _prendre(fd)
        except (BlockingIOError, PermissionError) as exc:
            raise RenduDejaEnCours(
                f"un rendu de ce clip est deja en cours ({job_dir / NOM} tenu par un autre "
                "process) : attendez qu'il finisse") from exc
        try:
            yield Verrou(fd)
        finally:
            _rendre(fd)
    finally:
        os.close(fd)
