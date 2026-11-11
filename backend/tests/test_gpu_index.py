import subprocess

import pytest

from clipforge import gpu
from clipforge.config import _gpu_index


def test_gpu_index_vaut_zero_sans_variable():
    assert _gpu_index(None) == 0
    assert _gpu_index("") == 0
    assert _gpu_index("  ") == 0


def test_gpu_index_lit_un_entier():
    assert _gpu_index("1") == 1


def test_gpu_index_illisible_arrete_le_moteur():
    with pytest.raises(RuntimeError, match="CLIPFORGE_GPU_INDEX"):
        _gpu_index("un")  # un mot n'est pas un index ; "3060" en serait un (valide, mais faux)


def _faux_run(sortie: str):
    def run(cmd, **kwargs):
        assert "-i" in cmd and cmd[cmd.index("-i") + 1] == "1"
        return subprocess.CompletedProcess(cmd, 0, stdout=sortie, stderr="")
    return run


def test_vram_libre_lit_la_carte_demandee(monkeypatch):
    monkeypatch.setattr(gpu.shutil, "which", lambda _: "nvidia-smi")
    assert gpu.vram_libre_gb(1, run=_faux_run("6144\n")) == pytest.approx(6.0)


def test_vram_libre_rend_none_sans_nvidia_smi(monkeypatch):
    monkeypatch.setattr(gpu.shutil, "which", lambda _: None)
    assert gpu.vram_libre_gb(1) is None


def test_vram_libre_rend_none_sur_sortie_illisible(monkeypatch):
    monkeypatch.setattr(gpu.shutil, "which", lambda _: "nvidia-smi")
    assert gpu.vram_libre_gb(1, run=_faux_run("N/A\n")) is None


def test_verifier_vram_refuse_avec_les_chiffres(monkeypatch):
    monkeypatch.setattr(gpu.shutil, "which", lambda _: "nvidia-smi")
    with pytest.raises(gpu.VramInsuffisante, match="cuda:1 : 4.0 GiB libres, 5.4 GiB requis"):
        gpu.verifier_vram(1, 5.4, run=_faux_run("4096\n"))


def test_verifier_vram_accepte_quand_ca_tient(monkeypatch):
    monkeypatch.setattr(gpu.shutil, "which", lambda _: "nvidia-smi")
    assert gpu.verifier_vram(1, 5.4, run=_faux_run("12288\n")) == pytest.approx(12.0)


def test_verifier_vram_laisse_passer_si_non_mesurable(monkeypatch):
    monkeypatch.setattr(gpu.shutil, "which", lambda _: None)
    assert gpu.verifier_vram(1, 99.0) is None
