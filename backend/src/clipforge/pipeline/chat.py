import subprocess
from pathlib import Path

from clipforge import config


class ChatDownloadError(RuntimeError):
    pass


def download_chat(twitch_vod_id: str, dest: Path) -> Path:
    cli = Path(config.TWITCHDOWNLOADER_CLI)
    if not cli.is_file():
        raise ChatDownloadError(
            f"TwitchDownloaderCLI introuvable : {cli} (variable TWITCHDOWNLOADER_CLI)")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part.json")
    tmp.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            [str(cli), "chatdownload", "--id", twitch_vod_id, "-o", str(tmp)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=1800, check=False)
    except subprocess.TimeoutExpired as exc:
        tmp.unlink(missing_ok=True)
        raise ChatDownloadError(f"chatdownload timeout apres 1800s (vod {twitch_vod_id})") from exc
    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise ChatDownloadError(f"chatdownload a echoue : {(proc.stderr or '')[-400:]}")
    tmp.replace(dest)
    return dest
