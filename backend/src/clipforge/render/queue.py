import logging
import subprocess
from pathlib import Path

from clipforge import db as dbmod
from clipforge.pipeline.probe import ProbeUnavailable
from clipforge.render.ffmpeg_cmd import build_render_cmd
from clipforge.render.livraison import ecarter_orphelins, livrer, mettre_de_cote, part_path
from clipforge.render.montage_plan import ASS_NAME, build_montage_options
from clipforge.render.naming import livrable_du_clip, output_target
from clipforge.render.output_check import controler, durees_attendues
from clipforge.render.verrou import RenduDejaEnCours, Verrou, verrou_du_clip

logger = logging.getLogger(__name__)

CROP_TOLERANCE_PX = 2
# Large pour un clip d'une minute, meme en repli CPU sur une machine chargee.
# Ce qu'il borne n'est pas la lenteur, c'est le blocage : voir _run_ffmpeg.
RENDER_TIMEOUT_S = 600


def validate_profile_against_source(profile: dict, src_w: int, src_h: int) -> None:
    """Le profil doit tenir dans la source (tolerance d'arrondi RegionSelector : 2 px)."""
    for zone in ("cam", "game"):
        x, y = profile[f"{zone}_x"], profile[f"{zone}_y"]
        w, h = profile[f"{zone}_w"], profile[f"{zone}_h"]
        if (w > src_w or h > src_h
                or x + w > src_w + CROP_TOLERANCE_PX or y + h > src_h + CROP_TOLERANCE_PX):
            raise ValueError(
                f"Profil de scene ({w}x{h}) incompatible avec la source "
                f"{src_w}x{src_h} — retracer les zones")


def _run_ffmpeg(cmd: list[str], job_dir: Path) -> tuple[int, str]:
    """Lance ffmpeg sous borne de temps. Rend (code de retour, message d'erreur).

    Un rendu qui ne rend jamais la main n'est pas un cas d'ecole : une entree
    `-loop 1` (le PNG de l'emoji) est un flux INFINI, et si l'overlay perd son
    `shortest=1`, ffmpeg encode sans fin — mesure : toujours vivant a 45 s sur
    un clip de 2 s. Sans borne, le clip reste en 'rendering' pour l'eternite,
    sans ligne d'erreur et sans rien de relancable depuis l'interface. Un
    depassement est donc traite comme n'importe quel echec de rendu : on le
    consigne et on laisse le repli CPU tenter sa chance.
    """
    try:
        proc = subprocess.run(cmd, cwd=job_dir, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False,
                              timeout=RENDER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 1, f"ffmpeg n'a pas rendu la main en {RENDER_TIMEOUT_S} s (rendu interrompu)"
    return proc.returncode, proc.stderr[-500:]


def render_clip_job(clip_id: int, db_path: str, jobs_dir: str, output_dir: str) -> None:
    """Point d'entree process enfant : rend UN clip, statut/erreur persistes en DB.

    Tout le rendu se fait sous le verrou du clip. Un second rendu du meme clip,
    lance par l'autre process (pool de l'API, pipeline d'analyse), repart sans
    rien ecrire, ni en base ni sur le disque : la ligne appartient au rendu en
    cours, qui la passera en 'done' ou en erreur (fix round 3, F1).
    """
    from clipforge import config
    config.setup_logging()
    job_dir = Path(jobs_dir) / f"clip{clip_id}"
    try:
        with verrou_du_clip(job_dir) as verrou:
            _rendre_sous_verrou(clip_id, Path(db_path), job_dir, output_dir, verrou)
    except RenduDejaEnCours as exc:
        logger.error("Rendu clip %s refuse : %s", clip_id, exc)
    finally:
        config.teardown_logging()


def _rendre_sous_verrou(clip_id: int, db_path: Path, job_dir: Path, output_dir: str,
                        verrou: Verrou) -> None:
    conn = dbmod.get_db(db_path)
    try:
        clip = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        if not clip:
            return
        conn.execute("UPDATE clips SET status='rendering', progress=0, error=NULL WHERE id=?",
                     (clip_id,))
        conn.commit()
        vod = dbmod.get_vod(conn, clip["vod_id"])
        profile = conn.execute("SELECT * FROM scene_profiles WHERE id=?",
                               (clip["profile_id"],)).fetchone()
        if profile is None:
            raise RuntimeError("Aucun profil de scene associe au clip")
        if vod["width"] and vod["height"]:
            validate_profile_against_source(dict(profile), vod["width"], vod["height"])
        montage = build_montage_options(conn, clip, vod, job_dir)
        prevu_s = montage.pop("output_duration_s")
        vise = output_target(conn, clip, clip_id, output_dir)
        deja_sien = Path(clip["output_path"]) if clip["output_path"] else None
        # ffmpeg ecrit sous un temporaire propre a CE clip : un rendu refuse ne
        # doit jamais se voir dans le dossier de sortie, ni ecraser le bon rendu
        # precedent, ni croiser le rendu d'un clip homonyme mene de front. Un
        # temporaire deja la vient d'un process interrompu : mis de cote.
        rendu = part_path(vise.parent, clip_id)
        anciens = [rendu, verrou.temporaire_precedent(),
                   part_path(deja_sien.parent, clip_id) if deja_sien else None]
        ecarter_orphelins(anciens, job_dir, clip_id)
        # Avant ffmpeg : si ce process meurt en pleine ecriture, la note dit ou
        # le temporaire est reste, meme si le dossier de sortie change ensuite.
        verrou.noter(rendu)
        attendus = durees_attendues(Path(vod["source_path"]), prevu_s, clip["end_s"])
        for use_nvenc in (True, False):
            essai = "nvenc" if use_nvenc else "cpu"
            cmd = build_render_cmd(Path(vod["source_path"]), rendu, ASS_NAME,
                                   start_s=clip["start_s"], end_s=clip["end_s"],
                                   profile=dict(profile), use_nvenc=use_nvenc, **montage)
            code, erreur = _run_ffmpeg(cmd, job_dir)
            if code == 0:
                try:
                    erreur = controler(rendu, attendus)
                except ProbeUnavailable as exc:
                    mettre_de_cote(rendu, job_dir, f"non-controle-{essai}")
                    raise RuntimeError(f"Rendu non controlable : {exc}") from exc
                if erreur is None:
                    # Le nom a remplacer se relit ici, au plus tard : entre le
                    # debut du rendu et sa promotion, un autre clip a pu prendre
                    # le livrable que cette ligne annonce encore (fix round 3, F3).
                    out = livrer(rendu, vise, livrable_du_clip(conn, clip_id,
                                                               clip["output_path"]), job_dir)
                    break
            ecarte = mettre_de_cote(rendu, job_dir, f"refus-{essai}")
            logger.warning("Rendu %s (nvenc=%s) : %s%s", clip_id, use_nvenc, erreur[-300:],
                           f" (essai mis de cote : {ecarte.name})" if ecarte else "")
        else:
            quoi = "ffmpeg a echoue" if code else "sortie refusee"
            raise RuntimeError(f"{quoi} (nvenc puis cpu) : {erreur}")
        # error=NULL : un second rendu du meme clip, refuse pendant celui-ci
        # (« deja en cours »), a pu ecrire son message sur la meme ligne.
        conn.execute("UPDATE clips SET status='done', progress=100, output_path=?, error=NULL "
                     "WHERE id=?", (str(out), clip_id))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("Rendu clip %s echoue : %s", clip_id, exc)
        conn.execute("UPDATE clips SET status='error', error=? WHERE id=?",
                     (str(exc), clip_id))
        conn.commit()
    finally:
        conn.close()
