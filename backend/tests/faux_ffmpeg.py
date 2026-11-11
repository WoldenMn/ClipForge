"""Espion de subprocess.run partage par les tests de rendu : ffmpeg
simule qui ecrit ce qu'on attend de lui, ffprobe qui relit ce qu'il a ecrit.

Sorti de conftest.py (revue finale V6, lot D) : conftest garde les fixtures,
ce module l'outil qu'elles installent.
"""
import json
import subprocess
import wave
from pathlib import Path

from clipforge.render import queue as queue_mod


class FauxFfmpeg:
    """Espion de subprocess.run : memorise les commandes, rend les codes voulus.

    Un rendu (.mp4) porte sa duree dans son contenu, ffprobe la relit (revue
    finale V6, lot C : render_clip_job verifie ce qu'il a rendu). `durees` :
    duree ecrite par rendu successif, None = la duree planifiee par
    build_montage_options ; `manque_video` : secondes absentes du seul flux
    video ; `manque_audio` : idem pour l'audio ; `sans_audio` : sortie sans
    piste audio ; `video_sans_duree` et `sortie_audio_sans_duree` : flux sans
    duree, ce que ffprobe rend d'un MKV ; `ecrit` a
    False : rc=0 sans rien ecrire ; `sonde_panne` : ffprobe absent, expire ou
    illisible ; `pendant_rendu` : appele une fois, le .part ecrit, avant que
    ce rendu rende la main. Un fichier illisible par cet espion est la
    SOURCE : elle repond `duree_source`, `fin_video_source`, `fin_audio_source`
    (fin du flux, pas sa duree), `debut_audio_source` et `source_sans_audio`,
    plus `debut_source` (format.start_time) et `debut_video_source` ; debuts et
    fins en temps absolus, tels que ffprobe les ecrit.
    `source_sans_duree_par_flux` : aucune duree par flux, ce que ffprobe rend
    d'un MKV ou d'un WebM. Les sondes ffprobe ne comptent pas dans `calls`."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.codes = [0]
        self.expire = False
        self.duree_wav: float | None = None
        self.durees: list[float | None] = [None]
        self.manque_video = 0.0
        self.manque_audio = 0.0
        self.sans_audio = False
        self.video_sans_duree = False
        self.sortie_audio_sans_duree = False
        self.ecrit = True
        self.sonde_panne: str | None = None
        self.duree_source = 600.0
        self.fin_video_source: float | None = None
        self.fin_audio_source: float | None = None
        self.debut_audio_source = 0.0
        self.debut_source = 0.0
        self.debut_video_source = 0.0
        self.source_sans_audio = False
        self.source_sans_duree_par_flux = False
        self.duree_planifiee: float | None = None
        self.rendus = 0
        self.pendant_rendu = None

    def installer(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", self.run)
        reel = queue_mod.build_montage_options

        def _montage(*args, **kwargs):
            options = reel(*args, **kwargs)
            self.duree_planifiee = options.get("output_duration_s")
            return options

        monkeypatch.setattr(queue_mod, "build_montage_options", _montage)

    def _sonde(self, cmd):
        if self.sonde_panne == "absente":
            raise FileNotFoundError("ffprobe")
        if self.sonde_panne == "expire":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=60)
        if self.sonde_panne == "illisible":
            return subprocess.CompletedProcess(cmd, 0, stdout="<html>", stderr="")
        cible = Path(cmd[-1])
        try:
            duree = float(cible.read_text(encoding="utf-8").removeprefix("mp4 "))
            video, audio = duree - self.manque_video, duree - self.manque_audio
            sans_audio, debut, debut_video, debut_audio = self.sans_audio, 0.0, 0.0, 0.0
            video = None if self.video_sans_duree else video
            audio = None if self.sortie_audio_sans_duree else audio
        except OSError:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No such file")
        except ValueError:
            # Pas un rendu de cet espion : c'est la SOURCE que le rendu sonde
            # pour savoir ou finissent vraiment ses flux (revue finale V6,
            # fix round 1, R2).
            duree, sans_audio = self.duree_source, self.source_sans_audio
            debut, debut_video = self.debut_source, self.debut_video_source
            fin_fichier = debut + duree
            fin = fin_fichier if self.fin_video_source is None else self.fin_video_source
            video = fin - debut_video
            fin = fin_fichier if self.fin_audio_source is None else self.fin_audio_source
            debut_audio = self.debut_audio_source
            audio = fin - debut_audio
            if self.source_sans_duree_par_flux:
                video = audio = None
        flux = [{"codec_type": "video", "width": 1080, "height": 1920,
                 "avg_frame_rate": "30/1", "start_time": str(debut_video)}]
        if video is not None:
            flux[0]["duration"] = str(video)
        if not sans_audio:
            flux.append({"codec_type": "audio", "start_time": str(debut_audio)})
            if audio is not None:
                flux[-1]["duration"] = str(audio)
        stdout = json.dumps({"streams": flux,
                             "format": {"start_time": str(debut), "duration": str(duree)}})
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    def run(self, cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return self._sonde(cmd)
        self.calls.append(list(cmd))
        self.kwargs.append(kwargs)
        if self.expire:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))
        code = self.codes[min(len(self.calls) - 1, len(self.codes) - 1)]
        if code == 0 and cmd[-1].endswith(".wav.part"):
            # extract_wav relit son WAV (revue finale V6, lot C) : un vrai WAV
            # silencieux, de la duree demandee par -t.
            secondes = float(cmd[cmd.index("-t") + 1]) if "-t" in cmd else 1.0
            secondes = secondes if self.duree_wav is None else self.duree_wav
            with wave.open(cmd[-1], "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(bytes(2 * round(secondes * 16000)))
        elif code == 0 and cmd[-1].endswith(".mp4") and self.ecrit:
            # ffmpeg pose son fichier de sortie (dernier argument). Sans cela,
            # aucun test ne verrait jamais deux clips viser le meme nom.
            self.rendus += 1
            duree = self.durees[min(self.rendus - 1, len(self.durees) - 1)]
            duree = self.duree_planifiee if duree is None else duree
            Path(cmd[-1]).write_text(f"mp4 {duree}", encoding="utf-8")
            if self.pendant_rendu is not None:
                # Ce qui se passe pendant que ce rendu tourne (un autre clip
                # rendu de front) : une seule fois, pas a chaque rendu.
                pendant, self.pendant_rendu = self.pendant_rendu, None
                pendant()
        elif code == 0 and self.ecrit:
            Path(cmd[-1]).write_bytes(b"mp4")
        return subprocess.CompletedProcess(args=cmd, returncode=code, stdout="",
                                           stderr="erreur simulee")

    @staticmethod
    def graphe(cmd: list[str]) -> str:
        """filter_complex d'une commande : en ligne, ou le fichier de -/filter_complex."""
        if "-/filter_complex" in cmd:
            return Path(cmd[cmd.index("-/filter_complex") + 1]).read_text(encoding="utf-8")
        return cmd[cmd.index("-filter_complex") + 1]

    @property
    def filter_complex(self) -> str:
        return self.graphe(self.calls[-1])
