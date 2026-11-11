"""Les huit etapes reelles du pipeline, cablees sur les modules testes unitairement.

Les imports lourds (faster_whisper, numpy...) restent DANS build_real_steps et
ses closures : le process parent importe ce module sans payer leur chargement.
"""
import json
import logging
from pathlib import Path

from clipforge import db as dbmod
from clipforge.pipeline import segment_step
from clipforge.pipeline.runner import StepFn, build_progress_cb

logger = logging.getLogger(__name__)


def build_real_steps() -> list[tuple[str, StepFn]]:
    """Etapes reelles cablees sur les modules testes unitairement."""
    from clipforge import config
    from clipforge.pipeline import (
        audio_analysis,
        audio_extract,
        autoclip,
        chat,
        chat_analysis,
        download,
        probe,
        remux,
        thumbs,
    )
    from clipforge.pipeline.ai_scoring import score_segments_ai
    from clipforge.pipeline.transcribe import transcribe_to_db

    def step_download(conn, vod_id, ctx):
        vod = dbmod.get_vod(conn, vod_id)
        if not vod["twitch_url"] or vod["source_path"]:
            return
        # Deux callbacks distincts, et non le meme deux fois : chacun tient sa
        # propre horloge de depart. La reparation repart de zero apres un
        # telechargement de plusieurs minutes ; partager l'horloge afficherait un
        # temps restant extrapole depuis le debut du telechargement.
        path, title, duree = download.download_vod(
            vod["twitch_url"], config.DOWNLOADS_DIR,
            progress_cb=build_progress_cb(conn, vod_id, "downloading"),
            repair_cb=build_progress_cb(conn, vod_id, "downloading"))
        # duration_s est renseignee des maintenant (mesuree a la validation du
        # telechargement) pour que l'etape de remux puisse chiffrer sa
        # progression ; step_probe la reecrira avec sa propre mesure.
        conn.execute("UPDATE vods SET source_path=?, title=?, duration_s=? WHERE id=?",
                     (str(path), title, duree or None, vod_id))
        conn.commit()

    def step_remux(conn, vod_id, ctx):
        """Rend la source seekable — par le navigateur, et par ffmpeg au rendu.

        Une capture HLS Twitch arrive en MP4 fragmente sans table d'index, ou en
        MPEG-TS brut : le navigateur ne sait pas convertir un instant en offset
        d'octets et devrait telecharger lineairement des gigaoctets.

        Le MPEG-TS, lui, ne se seeke pas non plus au rendu : `-ss X -i source.ts`
        rend l'image de la cle SUIVANTE pendant que l'audio part bien de X
        (mesure du 2026-09-20 : jusqu'a 3684 ms d'avance video, rc=0, et parfois
        aucune image). Il est donc repare meme quand il vient d'un import local —
        mais par une COPIE dans le dossier de donnees : le fichier de
        l'utilisateur n'est jamais reecrit, et c'est la copie que source_path
        designe ensuite.

        plan_de_remux dit ce qu'il y a a faire et comment. C'est le MEME controle
        que celui du point de reprise : l'etape ne peut donc pas decliner une
        reparation que la reprise vient d'annoncer.

        Quand la source elle-meme est morte (D8, mesure le 2026-08-30 : 52 Go
        relus pour avancer de 9,4 %, sortie refusee a 1620 s pour 17170 s
        attendues), il n'y a rien a reparer et rien a tolerer — accepter les
        paquets corrompus desynchroniserait l'image, le son et les sous-titres sur
        4 h 46. La seule issue saine est une source neuve : on ecarte le fichier,
        on vide source_path, et la reprise repart du telechargement.
        """
        vod = dbmod.get_vod(conn, vod_id)
        source = Path(vod["source_path"] or "")
        plan = remux.plan_de_remux(source, config.DOWNLOADS_DIR)
        if plan is None:
            return
        # Un import local garde son fichier : la version indexee est une COPIE
        # dans le dossier de donnees, et c'est elle que la suite du pipeline lit.
        copie = config.DOWNLOADS_DIR / f"vod{vod_id}.mp4" if plan.en_copie else None
        try:
            remux.ensure_seekable(source, duration_s=vod["duration_s"] or 0.0,
                                  progress_cb=build_progress_cb(conn, vod_id, "remuxing"),
                                  dest=copie)
            if copie is not None:
                conn.execute("UPDATE vods SET source_path=? WHERE id=?",
                             (str(copie), vod_id))
                conn.commit()
        except remux.SourceInexploitableError as exc:
            if not vod["twitch_url"]:
                raise      # import local : rien a retelecharger, sa copie reste la sienne
            ecarte = download.mettre_de_cote(source)
            conn.execute("UPDATE vods SET source_path='' WHERE id=?", (vod_id,))
            conn.commit()
            logger.warning("Source de la vod %s ecartee sous %s : %s",
                           vod_id, ecarte.name, exc)
            # Le detail technique garde les chiffres de la troncature ; le message
            # utilisateur, lui, sort de user_message sur « source ecartee ».
            raise remux.RemuxError(
                f"source ecartee sous {ecarte.name} apres : {exc}") from exc

    def step_probe(conn, vod_id, ctx):
        vod = dbmod.get_vod(conn, vod_id)
        info = probe.probe_media(vod["source_path"])
        conn.execute("UPDATE vods SET duration_s=?, width=?, height=?, fps=? WHERE id=?",
                     (info.duration_s, info.width, info.height, info.fps, vod_id))
        conn.commit()
        thumbs.try_thumb(config.JOBS_DIR, vod_id, Path(vod["source_path"]),
                         info.duration_s * thumbs.VOD_THUMB_RATIO)

    def _wav(vod_id: int) -> Path:
        return config.JOBS_DIR / f"vod{vod_id}" / "audio.wav"

    def step_extract(conn, vod_id, ctx):
        vod = dbmod.get_vod(conn, vod_id)
        wav = _wav(vod_id)
        if not wav.is_file():
            audio_extract.extract_wav(vod["source_path"], wav)

    def step_transcribe(conn, vod_id, ctx):
        import os

        vod = dbmod.get_vod(conn, vod_id)
        if vod["words_complete"]:
            return  # transcription COMPLETE (drapeau explicite) : ne JAMAIS re-transcrire 4h
        conn.execute("UPDATE vods SET words_complete=0 WHERE id=?", (vod_id,))
        conn.commit()
        model = (os.environ.get("CLIPFORGE_WHISPER_MODEL")
                 or dbmod.get_setting(conn, "whisper_model") or "large-v3")
        from clipforge.gpu_lease import bail_gpu

        # La carte est fixee par CLIPFORGE_GPU_INDEX ; le bail empeche juste
        # qu'une autre charge (Flux, LLM) s'y superpose pendant la transcription.
        with bail_gpu(model):
            transcribe_to_db(conn, vod_id, _wav(vod_id), model_name=model,
                             progress_cb=build_progress_cb(conn, vod_id, "transcribing"))
        conn.execute("UPDATE vods SET words_complete=1 WHERE id=?", (vod_id,))
        conn.commit()

    def step_analyze(conn, vod_id, ctx):
        """Analyse audio et chat en parallele : aucune ne depend de l'autre."""
        from concurrent.futures import ThreadPoolExecutor
        results = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(_audio_scores, vod_id): "audio",
                       pool.submit(_chat_scores, conn, vod_id): "chat"}
            for fut, kind in futures.items():
                results[kind] = fut.result()
        for kind, scores in results.items():
            if scores is None:
                continue
            conn.execute("DELETE FROM signals WHERE vod_id=? AND kind=?", (vod_id, kind))
            conn.executemany("INSERT INTO signals VALUES (?,?,?,?)",
                             [(vod_id, kind, t, s) for t, s in scores])
        conn.commit()

    def _audio_scores(vod_id):
        return audio_analysis.compute_audio_scores(_wav(vod_id))

    def _chat_scores(conn, vod_id):
        vod = dbmod.get_vod(conn, vod_id)
        if not vod["twitch_vod_id"]:
            return None
        dest = config.JOBS_DIR / f"vod{vod_id}" / "chat.json"
        try:
            if not dest.is_file():
                chat.download_chat(vod["twitch_vod_id"], dest)
            msgs = chat_analysis.parse_chat_json(dest)
            return chat_analysis.compute_chat_scores(msgs, duration_s=vod["duration_s"])
        except (chat.ChatDownloadError, json.JSONDecodeError) as exc:
            logger.warning("Chat indisponible pour vod %s : %s", vod_id, exc)
            return None

    def step_ai(conn, vod_id, ctx):
        import os

        def note(msg: str) -> None:
            conn.execute("UPDATE vods SET ai_note=? WHERE id=?", (msg, vod_id))
            conn.commit()

        if dbmod.get_setting(conn, "ai_enabled") != "1":
            return
        if not os.environ.get("ANTHROPIC_API_KEY"):
            note("Scoring IA active mais ANTHROPIC_API_KEY absente : classement local utilise")
            return
        if not score_segments_ai(conn, vod_id):
            note("API Claude indisponible : classement local utilise")
        else:
            conn.execute("UPDATE vods SET ai_note=NULL WHERE id=?", (vod_id,))
            conn.commit()

    # Les trois dernieres vivent dans autoclip.py : elles n'ont besoin d'aucune
    # des closures ci-dessus, et les garder ici pousserait ce fichier au-dela de
    # ce qui se relit d'un trait.
    return [("downloading", step_download), ("remuxing", step_remux),
            ("probing", step_probe),
            ("extracting_audio", step_extract), ("transcribing", step_transcribe),
            ("analyzing", step_analyze),
            ("segmenting", segment_step.step_segment), ("ai_scoring", step_ai),
            ("making_clips", autoclip.step_autoclips),
            ("writing_hooks", autoclip.step_writing),
            ("rendering_clips", autoclip.step_autorender)]
