"""Ce que le conteneur d'un fichier dit de lui-meme, sans jamais l'ecrire.

Tout ce module est en lecture seule : il ouvre le fichier, lit ses premiers
octets et nomme le defaut. Rien ici n'appelle ffmpeg ni ne touche au disque —
c'est remux_repair.py qui repare ce que ce module a diagnostique.
"""
import logging
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

ATOM_HEADER = 8
MAX_ATOMS_SCANNED = 32   # moov puis moof arrivent dans les tout premiers atomes
TS_PACKET = 188          # taille d'un paquet MPEG-TS, constante du format
TS_SYNC = 0x47           # octet de synchro en tete de chaque paquet
TS_PACKETS_CHECKED = 8


def _atoms(f, end: int, limit: int = MAX_ATOMS_SCANNED) -> Iterator[tuple[str, int, int]]:
    """Itere (type, debut du contenu, fin) sur un niveau d'atomes ISO-BMFF.

    Ne lit que les en-tetes : le curseur est repositionne a chaque tour, ce qui
    laisse l'appelant libre de descendre dans un atome en cours d'iteration.
    """
    pos = f.tell()
    for _ in range(limit):
        if pos >= end:
            return
        f.seek(pos)
        head = f.read(ATOM_HEADER)
        if len(head) < ATOM_HEADER:
            return
        size = struct.unpack(">I", head[:4])[0]
        typ = head[4:8].decode("latin-1", "replace")
        body = pos + ATOM_HEADER
        if size == 1:                                   # taille 64 bits
            size = struct.unpack(">Q", f.read(8))[0]
            body += 8
        elif size == 0:                                 # atome final, court jusqu'a la fin
            size = end - pos
        if size < ATOM_HEADER:
            return
        yield typ, body, pos + size
        pos += size


def is_fragmented(path: Path) -> bool:
    """True si le fichier est un MP4 fragmente SANS table d'index de segments.

    C'est exactement le cas illisible par un navigateur : sans sidx, convertir un
    instant en offset d'octets demanderait de parcourir lineairement les fragments,
    donc des gigaoctets. Un MP4 classique (moov indexe) et un MP4 fragmente AVEC
    sidx renvoient tous les deux False : ils se seekent par requetes Range.

    Tout ce qui n'est pas un MP4 (mkv, avi, ts) renvoie False : ces conteneurs ne
    relevent pas de ce diagnostic, et on ne touche jamais a un fichier qu'on ne
    sait pas relire.
    """
    try:
        end = path.stat().st_size
        with path.open("rb") as f:
            fragmente = False
            for i, (typ, body, stop) in enumerate(_atoms(f, end)):
                if i == 0 and typ != "ftyp":
                    return False                        # pas un MP4
                if typ == "sidx":
                    return False                        # index present : seekable
                if typ == "moov":
                    f.seek(body)
                    fragmente = any(t == "mvex" for t, _, _ in _atoms(f, stop))
                elif typ == "moof":
                    return fragmente                    # 1er fragment atteint, aucun sidx avant
            return False
    except (OSError, struct.error) as exc:
        logger.warning("Conteneur de %s illisible, remux ignore : %s", path.name, exc)
        return False


def is_mpegts(path: Path) -> bool:
    """True si le fichier est un flux de transport MPEG, quelle que soit son extension.

    Le HLS Twitch arrive en MPEG-TS sous un nom .mp4 : sans cette reconnaissance,
    is_fragmented ne voyait qu'un premier atome different de `ftyp` et laissait
    passer un conteneur que ffprobe ne sait pas dater (test reel du 2026-08-30).

    Reconnu aux octets magiques et NON par ffprobe : c'est justement l'outil qui
    ne sait pas decrire ce fichier (il y repond `duration=N/A`), et le sonder
    coute une lecture qu'on cherche precisement a ne plus subir. Un 0x47 en tete
    arrive par hasard ; huit, espaces d'exactement 188 octets, non. Les variantes
    a 192 octets (M2TS) ne sont pas reconnues : aucun cas reel derriere.
    """
    try:
        with path.open("rb") as f:
            head = f.read(TS_PACKET * TS_PACKETS_CHECKED)
    except OSError as exc:
        logger.warning("Conteneur de %s illisible, remux ignore : %s", path.name, exc)
        return False
    paquets = len(head) // TS_PACKET
    return paquets >= 2 and all(head[i * TS_PACKET] == TS_SYNC for i in range(paquets))


def besoin_de_remux(path: Path) -> str | None:
    """Le defaut de conteneur a reparer, ou None s'il n'y a rien a faire.

    Deux defauts seulement, les deux mesures sur des VOD reelles : "mpegts" (le
    flux brut livre par le HLS Twitch) et "fragmente" (un MP4 fragmente sans
    table d'index). Tout le reste — mkv, avi, MP4 sain — est laisse intact : on
    ne reecrit jamais un conteneur qu'aucun incident ne designe.
    """
    if is_mpegts(path):
        return "mpegts"
    return "fragmente" if is_fragmented(path) else None


class PlanDeRemux(NamedTuple):
    """Ce que l'etape de remux fera de cette source : quel defaut, et comment."""

    defaut: str        # "mpegts" ou "fragmente"
    en_copie: bool     # True : ecrire une copie ailleurs, laisser l'original intact


def plan_de_remux(path: Path, downloads_dir: Path) -> PlanDeRemux | None:
    """Le travail que l'etape de remux fera VRAIMENT sur cette source, ou None.

    Site unique du calcul, pour l'etape elle-meme et pour le point de reprise qui
    refuse de sauter par-dessus une source encore malade. Les separer ferait
    promettre a la reprise une reparation que l'etape declinerait ensuite : c'est
    exactement le defaut mesure le 2026-08-30, un message qui annonce « ClipForge
    le reparera » devant un chemin qui ne repare rien.

    Deux regimes, selon a qui appartient le fichier.

    Telecharge par ClipForge : il est a nous, les deux defauts se reparent en le
    remplacant.

    Importe par l'utilisateur : son fichier n'est jamais reecrit, et seul le
    MPEG-TS justifie d'en tirer une copie. Mesure du 2026-09-20 sur une source a
    GOP de 4,167 s : `-ss 21.337 -i source.ts` rend l'image de 25.021 s sous un
    audio parti de 21.337 s, soit 3684 ms de decalage a rc=0, et `-ss 38.3` ne
    rend aucune image — tout clip rendu depuis un TS importe est donc faux. Le
    meme flux en MP4 indexe seeke a 0 ms. Un MP4 fragmente importe, lui, seeke a
    25 ms pres : son defaut ne gene que l'apercu du navigateur, ce qui ne vaut
    pas de dupliquer plusieurs gigaoctets sur le disque de l'utilisateur.

    downloads_dir est passe en parametre et NON lu depuis config : le dossier de
    donnees se deplace d'un lancement a l'autre (CLIPFORGE_DATA), et chaque
    appelant lit deja le sien au moment de l'appel.
    """
    if not path.is_file():
        return None
    defaut = besoin_de_remux(path)
    if defaut is None:
        return None
    try:
        a_nous = path.resolve().is_relative_to(downloads_dir.resolve())
    except OSError:
        return None
    if a_nous:
        return PlanDeRemux(defaut, en_copie=False)
    return PlanDeRemux(defaut, en_copie=True) if defaut == "mpegts" else None


def remux_prevu(path: Path, downloads_dir: Path) -> str | None:
    """Le defaut que l'etape de remux reparerait sur cette source, ou None.

    Enveloppe de plan_de_remux pour les appelants que seule la question « reste-
    t-il quelque chose a faire ? » interesse.
    """
    plan = plan_de_remux(path, downloads_dir)
    return plan.defaut if plan else None
