import re
from collections.abc import Callable
from pathlib import Path

from clipforge.pipeline import remux
from clipforge.pipeline.probe import ProbeError, probe_media


class DownloadError(RuntimeError):
    pass


def twitch_vod_id(url: str) -> str | None:
    m = re.match(r"^https?://(www\.|m\.)?twitch\.tv/videos/(\d+)", url)
    return m.group(2) if m else None


def hook_progress_pct(d: dict) -> float | None:
    """Pourcentage depuis un event de hook yt-dlp, ou None si inconnu.

    Le HLS Twitch fragmente sans total_bytes : repli sur fragment_index/count.
    """
    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
    if total:
        return min(100.0, d.get("downloaded_bytes", 0) / total * 100.0)
    fi, fc = d.get("fragment_index"), d.get("fragment_count")
    if fi and fc:
        return min(100.0, fi / fc * 100.0)
    return None


def mettre_de_cote(path: Path) -> Path:
    """Ecarte une source inexploitable sous `<nom>.corrompu.<ext>`. Rend son nouveau chemin.

    Ici et non dans remux.py : ce n'est pas une reparation de conteneur, c'est
    liberer le nom pour qu'un telechargement neuf puisse reprendre la place. Le
    module qui fait entrer la source est celui qui la range de cote.

    RENOMMER et jamais supprimer. Le fichier pese des gigaoctets, il porte
    peut-etre la seule copie d'un live, et rien ne dit que le retelechargement
    aboutira : c'est a l'utilisateur d'en decider, pas au pipeline. Le rang suit
    la convention du dossier de sortie (`nom (2).mp4`) — deux telechargements qui
    echouent au meme endroit ne doivent pas s'effacer l'un l'autre.
    """
    vise = path.with_name(f"{path.stem}.corrompu{path.suffix}")
    candidat, rang = vise, 1
    while candidat.exists():
        rang += 1
        candidat = vise.with_name(f"{vise.stem} ({rang}){vise.suffix}")
    path.rename(candidat)
    return candidat


def _reparer_et_valider(path: Path, duree_annoncee: float,
                        repair_cb: Callable[[float], None] | None) -> float:
    """Repare puis CONTROLE le fichier avant de declarer le telechargement reussi.

    C'est le garde-fou qui manquait. yt-dlp livre le HLS Twitch en MPEG-TS sous
    un nom .mp4, et a la reprise il voit le fichier deja present : il saute alors
    telechargement ET reparation, sans rien dire. `prepare_filename` suffisait a
    faire passer ce flux brut pour un succes, la base enregistrait sa source, et
    tout le reste du pipeline s'en etranglait, indefiniment, une reprise apres
    l'autre (mesure du 2026-08-30, six secondes par tentative).

    Retourne la duree MESUREE, qui vaut mieux que celle annoncee.
    """
    try:
        remux.ensure_seekable(path, duration_s=duree_annoncee, progress_cb=repair_cb)
        duree = probe_media(path).duration_s
        if duree <= 0:
            raise ProbeError(f"Duree nulle mesuree sur {path.name}")
    except (remux.RemuxError, ProbeError) as exc:
        raise DownloadError(
            f"Telechargement incomplet : {path.name} n'est pas une video lisible. "
            f"Relance l'analyse pour reprendre le telechargement. Detail : {exc}") from exc
    return duree


def download_vod(url: str, dest_dir: Path, *,
                 progress_cb: Callable[[float], None] | None = None,
                 repair_cb: Callable[[float], None] | None = None
                 ) -> tuple[Path, str, float]:
    """Telecharge une VOD Twitch, la repare et la valide. Rend (chemin, titre, duree).

    repair_cb suit la passe de reparation, qui repart de zero. Lui passer le meme
    callback que le telechargement ferait extrapoler son temps restant depuis le
    debut du telechargement, donc afficher n'importe quoi.
    """
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    final: dict = {}
    last = [-1.0]

    def hook(d: dict) -> None:
        if d["status"] == "downloading" and progress_cb:
            pct = hook_progress_pct(d)
            if pct is not None and pct - last[0] >= 0.5:
                last[0] = pct
                progress_cb(pct)
        elif d["status"] == "finished":
            final["path"] = d["filename"]

    opts = {
        "format": "best",
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "windowsfilenames": True,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
        "noplaylist": True,
        "quiet": True,
        # 1,9 Mo de lignes « [download] … » mesurees dans le journal du moteur sur
        # une seule VOD de 4 h 46, soit 99 % du fichier.
        "noprogress": True,
        # Reparation post-telechargement rendue a ClipForge. Celle de yt-dlp
        # (FixupM3u8, `ffmpeg -map 0`) embarque les flux de donnees injectes par
        # les coupures publicitaires Twitch, ne converge pas (90 Go relus pour
        # 19,5 Go de source) et n'a aucun delai maximum. _reparer_et_valider
        # reprend la main derriere, avec un mappage explicite et une borne.
        "fixup": "never",
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        raise DownloadError(f"Telechargement echoue : {exc}") from exc
    path = Path(final.get("path") or ydl.prepare_filename(info))
    if not path.is_file():
        raise DownloadError(f"Fichier telecharge introuvable : {path}")
    duree = _reparer_et_valider(path, float(info.get("duration") or 0.0), repair_cb)
    return path, info.get("title") or path.stem, duree
