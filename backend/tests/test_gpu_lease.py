import pytest

from clipforge import gpu_lease


@pytest.fixture(autouse=True)
def service_declare(monkeypatch):
    """Redeclare une URL pour ce module.

    Le conftest coupe le bail pour toute la suite (`GPUQ_URL=""`), ce qui est
    voulu partout ailleurs : aucun test ne doit dependre d'un service externe.
    Mais ce module teste justement la mecanique du bail, il lui faut donc une
    URL. Aucun appel reseau n'en sort pour autant : chaque test injecte son
    propre `http`.
    """
    monkeypatch.setenv("GPUQ_URL", "http://service-de-test")


def _service(states, *, post_status=202):
    """Faux gpu-queue : POST renvoie le job, GET deroule `states`, DELETE compte."""
    calls = []
    reponses = iter(states[1:])  # l'etat initial est celui rendu par le POST

    def http(method, url, body):
        calls.append((method, url.rsplit("/", 1)[-1], body))
        if method == "POST":
            return post_status, {"id": "j1", "state": states[0]} if post_status == 202 else {}
        if method == "GET":
            return 200, {"id": "j1", "state": next(reponses), "gpu": 0}
        return 200, {"id": "j1", "state": "done"}

    return http, calls


def test_bail_attend_puis_execute_puis_libere():
    http, calls = _service(["queued", "queued", "running"])
    with gpu_lease.bail_gpu("large-v3", http=http, dormir=lambda _: None) as job_id:
        assert job_id == "j1"
    assert calls[0][0] == "POST" and calls[0][2]["kind"] == "lease"
    assert calls[0][2]["vram_gb"] == 5.4
    assert [c[0] for c in calls].count("GET") == 2
    assert calls[-1][0] == "DELETE"


def test_bail_libere_meme_si_le_bloc_leve():
    http, calls = _service(["running"])
    with pytest.raises(RuntimeError), gpu_lease.bail_gpu("small", http=http, dormir=lambda _: None):
        raise RuntimeError("transcription cassee")
    assert calls[-1][0] == "DELETE"


def test_service_injoignable_transcrit_sans_bail(caplog):
    def http(method, url, body):
        raise OSError("connexion refusee")

    with caplog.at_level("WARNING"), gpu_lease.bail_gpu("large-v3", http=http) as job_id:
        assert job_id is None
    assert "sans bail" in caplog.text


def test_refus_du_service_transcrit_sans_bail(caplog):
    http, _ = _service(["queued"], post_status=503)
    with caplog.at_level("WARNING"), gpu_lease.bail_gpu("large-v3", http=http) as job_id:
        assert job_id is None
    assert "HTTP 503" in caplog.text


def test_attente_trop_longue_transcrit_sans_bail_et_libere(caplog):
    """Une attente sans fin est pire que la collision qu'elle evite.

    Le bail est une optimisation : si la carte ne se libere pas, on transcrit
    quand meme. L'ancienne version levait TimeoutError apres une heure, ce qui
    transformait un bail abandonne en blocage silencieux de tout le pipeline.
    """
    http, calls = _service(["queued"] * 50)
    t = iter(range(0, 10_000, 100))
    with caplog.at_level("WARNING"), gpu_lease.bail_gpu(
        "large-v3", http=http, attente_max_s=250,
        horloge=lambda: next(t), dormir=lambda _: None,
    ) as job_id:
        assert job_id is None, "sans bail accorde, le bloc doit le savoir"
    assert "sans bail" in caplog.text
    assert calls[-1][0] == "DELETE", "le bail en attente doit etre rendu"


def test_url_vide_desactive_le_bail_sans_aucun_appel(monkeypatch):
    """Interrupteur des tests : aucun appel reseau ne doit partir.

    Pose la variable d'environnement, pas l'attribut de module : c'est
    ce chemin-la que les fixtures empruntent, et c'est lui qui etait
    casse tant que l'URL etait lue a l'import.
    """
    appels = []
    monkeypatch.setenv("GPUQ_URL", "")
    with gpu_lease.bail_gpu("large-v3", http=lambda *a: appels.append(a)) as job_id:
        assert job_id is None
    assert appels == []


def test_prefer_suit_l_index_de_carte(monkeypatch):
    monkeypatch.setattr(gpu_lease.config, "WHISPER_GPU_INDEX", 1)
    assert gpu_lease._prefer() == "compute"
    monkeypatch.setattr(gpu_lease.config, "WHISPER_GPU_INDEX", 0)
    assert gpu_lease._prefer() == "display"
