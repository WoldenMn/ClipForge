"""Genere tests/fixtures/fixture.mp4 (30 s, 1920x1080, bursts audio a 8s et 18s)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

if shutil.which("ffmpeg") is None:
    sys.exit("ffmpeg introuvable dans le PATH — requis pour generer la fixture")

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "fixture.mp4"
OUT.parent.mkdir(parents=True, exist_ok=True)
TMP = OUT.with_name(f"{OUT.stem}.tmp{OUT.suffix}")  # ffmpeg deduit le conteneur de l'extension

AUDIO = ("aevalsrc=0.04*sin(440*2*PI*t)"
         "+0.6*sin(880*2*PI*t)*(gt(mod(t\\,10)\\,8))"
         ":d=30:s=44100")
subprocess.run([
    "ffmpeg", "-y",
    "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=30",
    "-f", "lavfi", "-i", AUDIO,
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "33", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-shortest", "-f", "mp4", str(TMP),
], check=True)
os.replace(TMP, OUT)
print(f"fixture: {OUT}")
