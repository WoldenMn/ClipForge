"""Un clip n'est rendu qu'une fois a la fois (revue finale V6, fix round 3, F1).

Mesure du relecteur, vraie application et vrai pool : VOD en erreur apres un
redemarrage pendant le montage, quatre clips auto en erreur. « Tout relancer »
(POST /render sur chacun) puis « Relancer l'analyse » : tout repondait 200, le
pool ET l'etape de montage du pipeline rendaient les memes clips. Le clip 4
etait livre deux fois (5 fois sur 5) ; deux departs quasi simultanes
laissaient un fichier corrompu, marque 'done' une fois sur trois.
"""
import concurrent.futures
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_render_queue import _clip, _clone_de_clip, _rendre, _seed

import clipforge
from clipforge import db as dbmod


class FileDAttente(concurrent.futures.Executor):
    """Le pool, sans ses process : ce qui est soumis attend qu'on le lance."""

    def __init__(self):
        self.taches: list[tuple[concurrent.futures.Future, object, tuple]] = []

    def submit(self, fn, *args, **kwargs):
        fut = concurrent.futures.Future()
        self.taches.append((fut, fn, args))
        return fut

    def lancer(self, qui) -> None:
        for fut, fn, args in [t for t in self.taches if t[1] is qui]:
            self.taches.remove((fut, fn, args))
            fut.set_result(fn(*args))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_DATA", str(tmp_path))
    from clipforge import config
    importlib.reload(config)
    from clipforge import main as mainmod
    importlib.reload(mainmod)
    with TestClient(mainmod.app, base_url="http://127.0.0.1:8765") as c:
        yield c


def _vod_interrompue_au_montage(tmp_path: Path) -> list[int]:
    """Quatre clips auto en erreur sur une VOD tombee pendant le montage."""
    db_path = tmp_path / "clipforge.db"
    ids = [_seed(db_path)]
    ids += [_clone_de_clip(db_path, ids[0]) for _ in range(3)]
    conn = dbmod.get_db(db_path)
    try:
        conn.execute("UPDATE clips SET status='error', error='interrompu par redemarrage', "
                     "origin='auto'")
        conn.execute("UPDATE vods SET status='error', error='Analyse interrompue', "
                     "error_detail='rendering_clips: interrompu par redemarrage'")
        conn.commit()
    finally:
        conn.close()
    return ids


def test_tout_relancer_puis_relancer_l_analyse_rend_chaque_clip_une_fois(
        client, ffmpeg, tmp_path):
    """L'analyse prend un process du pool pendant que des rendus attendent
    encore le leur : c'est la que le pipeline les rendait une seconde fois."""
    from clipforge.pipeline.runner import run_analysis_pipeline
    from clipforge.render.queue import render_clip_job

    ids = _vod_interrompue_au_montage(tmp_path)
    pool = client.app.state.pool = FileDAttente()
    assert [client.post(f"/api/clips/{i}/render").status_code for i in ids] == [200] * 4
    analyse = client.post("/api/vods/1/analyze")
    pool.lancer(run_analysis_pipeline)
    pool.lancer(render_clip_job)

    assert len(ffmpeg.calls) == 4, "rendus lances pour quatre clips"
    lignes = [_clip(tmp_path, i) for i in ids]
    assert [c["status"] for c in lignes] == ["done"] * 4, [c["error"] for c in lignes]
    livres = sorted(p.name for p in (tmp_path / "output").iterdir())
    assert livres == sorted(Path(c["output_path"]).name for c in lignes), livres
    assert analyse.status_code == 409, analyse.text
    assert "relance l'analyse quand ils auront fini" in analyse.json()["detail"]

    assert client.post("/api/vods/1/analyze").status_code == 200
    pool.lancer(run_analysis_pipeline)
    assert len(ffmpeg.calls) == 4
    assert client.get("/api/vods/1").json()["status"] == "ready"


def test_un_second_rendu_du_meme_clip_repart_sans_rien_ecrire(tmp_path, ffmpeg, caplog):
    """Le second demarre pendant le premier, son temporaire deja ecrit : il ne
    lance pas ffmpeg, ne touche ni a la ligne ni au temporaire du premier."""
    clip_id = _seed(tmp_path / "clipforge.db")
    vu = {}

    def second_rendu():
        _rendre(tmp_path, clip_id)
        ligne = _clip(tmp_path, clip_id)
        vu.update(status=ligne["status"], error=ligne["error"], appels=len(ffmpeg.calls),
                  sorties=sorted(p.name for p in (tmp_path / "output").iterdir()))

    ffmpeg.pendant_rendu = second_rendu
    with caplog.at_level("ERROR"):
        _rendre(tmp_path, clip_id)

    assert vu == {"status": "rendering", "error": None, "appels": 1,
                  "sorties": [f"clipforge-c{clip_id}.part.mp4"]}, vu
    assert "un rendu de ce clip est deja en cours" in caplog.text, caplog.text
    clip = _clip(tmp_path, clip_id)
    assert clip["status"] == "done" and clip["error"] is None, clip["error"]
    assert sorted(p.name for p in (tmp_path / "output").iterdir()) == [
        Path(clip["output_path"]).name]
    assert list((tmp_path / "jobs" / f"clip{clip_id}").glob("*.mp4")) == []


TENIR = """import sys
from pathlib import Path
from clipforge.render.verrou import verrou_du_clip
with verrou_du_clip(Path(sys.argv[1])):
    print('tenu', flush=True)
    sys.stdin.readline()
"""


def test_un_rendu_tenu_par_un_autre_process_est_refuse_puis_passe_apres_lui(tmp_path, ffmpeg):
    """Le verrou vaut entre process, pas seulement dans celui-ci : c'est tout le
    sujet (le pool et le pipeline d'analyse n'en partagent aucun en memoire).
    Un autre process le tient : refus sans lancer ffmpeg et sans toucher a la
    ligne. Il finit, le verrou est rendu, le rendu passe.

    Le cas du process TUE (le systeme rend le verrou lui-meme) est mesure hors
    suite : le delai de liberation depend de la charge de la machine, un test
    dessus serait instable (mesure : 2,1 ms a vide)."""
    clip_id = _seed(tmp_path / "clipforge.db")
    env = dict(os.environ, PYTHONPATH=str(Path(clipforge.__file__).parents[1]))
    autre = subprocess.Popen([sys.executable, "-c", TENIR,
                              str(tmp_path / "jobs" / f"clip{clip_id}")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    try:
        assert autre.stdout.readline().strip() == "tenu"
        _rendre(tmp_path, clip_id)
        assert ffmpeg.calls == [] and _clip(tmp_path, clip_id)["status"] == "draft"
        assert not (tmp_path / "output").exists()
    finally:
        autre.communicate(input="va\n", timeout=30)
    assert autre.returncode == 0
    _rendre(tmp_path, clip_id)
    assert _clip(tmp_path, clip_id)["status"] == "done" and len(ffmpeg.calls) == 1
