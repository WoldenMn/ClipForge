"""Verdict sur le fichier rendu : tient-il la duree planifiee, flux par flux ?

Sorti de render/queue.py, qui orchestre un rendu et n'a pas a porter en plus
les regles du controle de sortie. Deplacer le fichier (temporaire, promotion,
mise de cote d'un refus) revient a render/livraison.py.
"""
import logging
from dataclasses import dataclass
from pathlib import Path

from clipforge.pipeline.probe import ProbeError, ProbeUnavailable, probe_media

logger = logging.getLogger(__name__)

# Ecart admis entre la duree planifiee et celle du fichier rendu (conteneur,
# flux video, flux audio). Rendus sains mesures contre leur duree planifiee :
# conteneur +0,017 a +0,070 s, video +0,000 a +0,027 s, audio +0,000 a
# +0,070 s sur les 6 rendus reels 1080p60 ; jusqu'a +0,140 s (conteneur et
# video) et +0,020 s (audio) sur les 15 rendus d'integration, ou une image
# vaut 0,2 s a 5 ips. Un morceau vide dans un trou video retire au moins
# MIN_SEG_S (0,6 s) a la video seule.
RENDER_DURATION_TOL_S = 0.25


@dataclass(frozen=True)
class Attendus:
    """Ce qu'un rendu doit tenir : la duree planifiee (plafond de chaque flux),
    un plancher par flux, si la source a une piste audio (None : pas sondee), et
    ce qu'il faut redire a l'utilisateur si le rendu est refuse."""
    planifie_s: float
    planchers: dict[str, float]
    audio_source: bool | None
    note: str | None = None


def durees_attendues(source: Path, prevu_s: float, clip_end_s: float) -> Attendus:
    """Plancher par flux : ce qu'un rendu peut perdre parce que la source n'a pas.

    Un flux qui s'arrete avant le FICHIER (video_courte : video 26,5 s sous un
    conteneur de 30 s) rend un dernier clip sain plus court que planifie ; ou
    pas du tout, si ce qui manque tombe dans une coupe (rendu a 200 segments),
    d'ou un plancher et non une cible decalee. Au-dela de la fin du fichier,
    rien n'est excuse : une fenetre qui deborde (vods.duration_s a 40 pour un
    fichier de 30 s) laissait passer un clip de 0,5 s, et un V6 tronque de 3 s,
    en silence (fix round 2, K2). Plus de la moitie qui manque n'est plus un
    clip : le plancher ne descend jamais sous la moitie. Chaque plancher qui
    descend se journalise.
    """
    manques = dict.fromkeys(("video", "audio"), 0.0)
    audio_source, note = None, None
    try:
        info = probe_media(source)
    except ProbeError as exc:
        logger.warning("Source %s non sondable (%s) : duree attendue non bornee par la "
                       "fin reelle de ses flux", source.name, exc)
    else:
        audio_source = info.audio_present
        fin_fichier = min(clip_end_s, info.duration_s)
        if clip_end_s > info.duration_s:
            # Cas reel de la VOD 2 : 9644,16 s en base, 9580,07 s de fichier.
            # Le clip est borne sur la duree enregistree, pas sur le fichier.
            note = (f"le fichier s'arrete a {info.duration_s:.3f} s, avant la fenetre "
                    f"demandee ({clip_end_s:.3f} s)")
            logger.warning("Source %s : %s : la duree enregistree en base depasse celle du "
                           "fichier", source.name, note)
        for nom, fin in (("video", info.video_end_s), ("audio", info.audio_end_s)):
            if fin is not None:
                manques[nom] = max(0.0, fin_fichier - fin)
    planchers = {}
    for nom, manque in manques.items():
        planchers[nom] = max(prevu_s - manque, prevu_s / 2)
        if manque > 0:
            logger.warning(
                "Source %s : flux %s fini %.3f s avant la fin du clip (%.3f s) : rendu "
                "accepte des %.3f s au lieu de %.3f s%s", source.name, nom, manque, clip_end_s,
                planchers[nom], prevu_s,
                " (plus de la moitie manque : jamais sous la moitie)"
                if prevu_s - manque < prevu_s / 2 else "")
    # Le muxeur garde le flux le plus long : le conteneur ne raccourcit que si
    # les deux flux raccourcissent.
    planchers["conteneur"] = max(planchers["video"], planchers["audio"])
    return Attendus(prevu_s, planchers, audio_source, note)


def controler(rendu: Path, attendus: Attendus) -> str | None:
    """Raison de refuser un rendu sorti en rc=0, ou None s'il tient sa duree.

    rc=0 avec une sortie absente, vide ou courte n'est pas un succes : la
    video seule peut manquer (morceau vide dans un trou video de la source),
    l'audio seul aussi (sortie de 20 s dont la piste audio ne fait que 12 s,
    sonde du relecteur), d'ou le controle des deux flux en plus du conteneur.

    ProbeUnavailable n'est PAS un verdict sur le rendu : la sonde est en
    panne, l'appelant doit renoncer plutot que rendre une seconde fois.
    """
    if not rendu.is_file() or rendu.stat().st_size == 0:
        return f"aucune sortie ecrite ({rendu.name})"
    try:
        info = probe_media(rendu)
    except ProbeUnavailable:
        raise
    except ProbeError as exc:
        return f"sortie illisible : {exc}"
    if not info.audio_present:
        if attendus.audio_source:
            return "aucune piste audio dans la sortie, alors que la source en a une"
        # Le V5 mappe 0:a? : une source sans audio rend legitimement une video
        # seule. Ce qui n'est pas controle se dit, jamais en silence.
        logger.warning("Rendu %s sans piste audio : duree audio non controlee (%s)",
                       rendu.name, "source non sondee" if attendus.audio_source is None
                       else "source sans audio")
    for nom, duree in (("conteneur", info.duration_s), ("video", info.video_duration_s),
                       ("audio", info.audio_duration_s)):
        if duree is None and nom == "audio":
            continue
        plancher, plafond = attendus.planchers[nom], attendus.planifie_s
        if duree is None or not (plancher - RENDER_DURATION_TOL_S <= duree
                                 <= plafond + RENDER_DURATION_TOL_S):
            lue = "inconnue" if duree is None else f"{duree:.3f} s"
            fin = f" ({attendus.note})" if attendus.note else ""
            return f"duree {nom} {lue} pour {_attendu(plancher, plafond)}{fin}"
    return None


def _attendu(plancher: float, plafond: float) -> str:
    if plancher >= plafond - 1e-9:
        return f"{plafond:.3f} s planifiees"
    return f"{plancher:.3f} a {plafond:.3f} s attendues (la source s'arrete avant)"
