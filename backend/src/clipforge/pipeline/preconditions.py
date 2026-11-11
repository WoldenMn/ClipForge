"""Ce que chaque etape du pipeline exige pour demarrer, verifie AVANT elle.

Le point de reprise perdu et le profil de scene manquant de la V3 etaient tous
deux des etats attendus mais jamais verifies : echouer a mi-parcours d'une etape
de quarante minutes coute infiniment plus cher que refuser de la commencer.

Une exigence rend la RAISON du refus, redigee pour l'utilisateur, ou None si
rien n'empeche. Elle ne repare rien et n'ecrit rien : run_steps decide seul de
la suite. A cote de steps.py plutot que dedans : ces controles ne partagent
aucune des closures ni des imports lourds de build_real_steps.
"""
from pathlib import Path

from clipforge import config
from clipforge import db as dbmod
from clipforge.pipeline.runner import PreconditionFn


def _source_lisible(conn, vod_id: int) -> str | None:
    """Le fichier source est toujours la ou la base le dit.

    Un source_path vide n'est pas un manque : c'est le telechargement qui le
    remplit, et il n'a pas encore eu lieu. Ce controle ne garde que des etapes
    qui viennent APRES lui.
    """
    vod = dbmod.get_vod(conn, vod_id)
    if not vod or not vod["source_path"]:
        return None
    if Path(vod["source_path"]).is_file():
        return None
    return "le fichier source est introuvable (deplace ou supprime)"


def _audio_extrait(conn, vod_id: int) -> str | None:
    """Le wav produit par l'extraction est present.

    JOBS_DIR est lu a l'appel et non a la construction : le dossier de donnees
    se deplace d'un lancement a l'autre (CLIPFORGE_DATA).
    """
    wav = config.JOBS_DIR / f"vod{vod_id}" / "audio.wav"
    return None if wav.is_file() else "l'audio n'a pas ete extrait"


PROFIL_A_TRACER = ("Trace un profil de scène dans l'éditeur une fois — "
                   "il s'appliquera ensuite tout seul.")
PROFIL_A_CHOISIR = ("Plusieurs profils de scène existent : choisis celui des clips "
                    "automatiques dans Réglages, « Profil des clips automatiques ».")


def _profil_manquant(conn) -> str:
    """Pourquoi aucun profil ne cadre les clips automatiques, dit au user."""
    nom = dbmod.get_setting(conn, "auto_profile")
    if nom:
        return (f"Le profil de scène « {nom} » choisi pour les clips automatiques "
                "n'existe plus : retrace-le ou choisis-en un autre.")
    n = conn.execute("SELECT COUNT(*) AS n FROM scene_profiles").fetchone()["n"]
    return PROFIL_A_TRACER if n == 0 else PROFIL_A_CHOISIR


def _mode_auto_coupe(conn) -> bool:
    """Reglage a 0 : les trois etapes automatiques n'ont rien a faire.

    Import tardif : preconditions.py est charge par le point d'entree du process
    enfant, autoclip tire toute la chaine de rendu derriere lui.
    """
    from clipforge.pipeline.autoclip import auto_clip_count

    return auto_clip_count(conn) <= 0


def _profil_pour_les_clips(conn, vod_id: int) -> str | None:
    """making_clips : un profil de scene, mais seulement s'il y a de quoi cadrer.

    Le profil est le SEUL geste que le mode automatique reclame encore, et il ne
    le reclame qu'une fois : le refuser ici vaut mieux que cinq clips sans
    cadrage dont chaque montage echouerait separement. C'est aussi le seul de ces
    etats que l'utilisateur puisse lever.

    Zero candidat, lui, ne se leve par AUCUN geste : ni tracer un profil, ni
    relancer — la reprise repointe sur making_clips et le decoupage ne rejoue
    jamais. Le refus enfermait donc dans une erreur definitive un stream sans
    moment fort, en lui coutant sa transcription : exactement le prix que la V4
    avait supprime. Rien a fabriquer n'exige rien ; les trois etapes ne feront
    rien et la VOD finira 'ready' avec une grille vide, comme avant la V5.
    """
    if _mode_auto_coupe(conn):
        return None
    candidats = conn.execute("SELECT COUNT(*) AS n FROM segments WHERE vod_id=?",
                             (vod_id,)).fetchone()["n"]
    if not candidats:
        return None
    return _profil_manquant(conn) if dbmod.clip_profile_id(conn, vod_id) is None else None


def build_preconditions() -> dict[str, PreconditionFn]:
    """Les exigences par nom d'etape. Une etape absente n'exige rien.

    Rien pour downloading ni remuxing : les deux se protegent deja seuls, en
    sortant sans rien faire quand leur entree manque. Rien non plus pour
    analyzing ni ai_scoring : aucun echec de la V3 n'y designe un etat attendu
    et non verifie, et poser un garde-fou sans incident derriere reviendrait a
    blinder au hasard ce que la phase 1 doit d'abord mesurer.

    Rien pour segmenting non plus : le decoupage ne consomme pas de mots. Il
    tourne sur les signaux audio et chat, narrative_bounds calant les bornes sur
    le pic quand la liste de mots est vide. Lui exiger une transcription
    transformait un resultat degrade en erreur SANS RETOUR : le refus repointe
    la reprise sur segmenting, step_transcribe ressort aussitot sur
    words_complete=1, et l'utilisateur n'avait plus qu'a supprimer sa VOD. Une
    exigence ne se pose que sur ce qui empeche VRAIMENT l'etape de demarrer, et
    seulement si l'utilisateur peut la lever.

    Rien pour writing_hooks : write_clips ne leve jamais et se replie seul sur la
    transcription quand l'API manque. Une exigence sur la cle transformerait un
    resultat degrade mais utilisable en analyse en erreur.

    Rien pour rendering_clips non plus. « Aucun clip automatique a monter » a ete
    pose puis RETIRE : step_autorender rend deja la main proprement sur un lot
    vide, tandis que le refus enfermait dans une erreur DEFINITIVE la VOD dont
    l'utilisateur venait de supprimer des montages rates — le geste normal apres
    un echec devenait irreversible. Une exigence qui ne protege de rien et
    qu'aucun geste ne leve n'est pas un garde-fou, c'est un cul-de-sac.
    """
    return {
        "probing": _source_lisible,
        "extracting_audio": _source_lisible,
        "transcribing": _audio_extrait,
        "making_clips": _profil_pour_les_clips,
    }
