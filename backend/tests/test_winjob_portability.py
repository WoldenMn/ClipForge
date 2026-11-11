"""L'import de winjob doit passer PARTOUT — la CI tourne sous Linux.

Volontairement SANS le skipif win32 de test_winjob.py : ce test protege
precisement la plateforme qui n'a pas WinDLL.
"""
import ctypes
import importlib
import sys


def test_the_module_imports_where_windll_does_not_exist(monkeypatch):
    """L'annotation de type `-> ctypes.WinDLL` etait evaluee a l'IMPORT du
    module : sous Linux (pas d'attribut WinDLL), tous les tests touchant
    main.py tombaient en erreur de setup. On rejoue Linux en retirant
    l'attribut (raising=False : il n'existe deja pas la-bas), puis on exige
    que l'import ET le no-op hors Windows passent."""
    monkeypatch.delattr(ctypes, "WinDLL", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    import clipforge.winjob as wj

    module = importlib.reload(wj)
    assert module.bind_children_to_engine() is False  # no-op hors Windows

    monkeypatch.undo()
    importlib.reload(module)  # restaure l'etat reel pour les tests suivants
