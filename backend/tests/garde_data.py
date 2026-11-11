"""Audit hook de la suite de tests : sous <racine du depot>/data, toute
ecriture de CE processus par l'une des primitives de `_SURVEILLEES` est refusee
avant d'avoir lieu, et consignee. Une primitive absente de cette liste passe :
la garde nomme ce qu'elle surveille plutot que de promettre tout.

Sorti de conftest.py comme faux_ffmpeg.py : conftest garde les fixtures et
les hooks pytest, ce module la mecanique. Un audit hook (PEP 578) ne se retire
pas : `armer` l'installe une fois pour le processus, `desarmer` le rend inerte
hors de la session. Il ne leve jamais pour un chemin hors de data/.

Aucun appel systeme par evenement : les formes de data/ (chemin long, resolu,
court 8.3) sont calculees une fois, a l'armement, puis chaque chemin est
compare en texte (abspath, casse repliee). Un realpath par evenement ouvrait
chaque fichier vise, sans partage, juste avant son renommage ou sa
suppression : une garde qui peut gener le code qu'elle surveille.
"""
import os
import sys

_ECRIT = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_PREFIXE_LONG = "\\\\?\\"
# Primitives surveillees, et pour chacune les arguments qui nomment une ecriture.
# os.symlink n'ecrit que sa destination : sa source est un chemin qu'il lit, qui
# n'a meme pas besoin d'exister. os.rename et os.link comptent leurs deux
# chemins, la source etant retiree ou devenant un second nom par lequel ecrire
# (hors Windows, livraison.renommer_sans_ecraser promeut par lien dur puis
# retrait).
_SURVEILLEES = {"os.mkdir": (0,), "os.remove": (0,), "os.rmdir": (0,),
                "os.truncate": (0,), "shutil.rmtree": (0,), "sqlite3.connect": (0,),
                "os.rename": (0, 1), "os.link": (0, 1), "os.symlink": (1,)}
_etat: dict = {"formes": (), "installe": False}
ecritures: list[str] = []


class EcritureDansData(Exception):
    """Ecriture sous data/ refusee. Volontairement pas un OSError : le code qui
    degrade proprement sur OSError (journal indisponible...) la prendrait pour
    une panne de disque et continuerait."""


def _texte(chemin: str) -> str:
    return os.path.normcase(os.path.abspath(chemin))


def _court(chemin: str) -> str | None:
    """Forme 8.3 d'un chemin existant (Windows), None ailleurs ou si refusee."""
    if os.name != "nt":
        return None
    import ctypes
    tampon = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(chemin, tampon, len(tampon))
    return tampon.value if 0 < n < len(tampon) else None


def _formes(donnees: str) -> tuple[str, ...]:
    """data/ sous toutes les formes qui y menent depuis sa racine : telle
    quelle, racine resolue (lien ou jonction), racine en noms courts. La
    racine existe toujours, data/ pas forcement : on nomme data/ apres."""
    racine, nom = os.path.split(os.path.abspath(donnees))
    racines = {racine, os.path.realpath(racine), _court(racine)}
    if os.name == "nt":
        racines |= {_PREFIXE_LONG + r for r in racines
                    if r and not r.startswith(_PREFIXE_LONG)}
    return tuple(sorted({_texte(os.path.join(r, nom)) + os.sep for r in racines if r}))


def _sans_uri(chemin):
    """Une URI sqlite (file:...?mode=rwc) nomme son fichier autrement qu'un
    chemin : sans ce decodage, une connexion par URI ecrirait sans etre vue."""
    if not isinstance(chemin, str) or not chemin.startswith("file:"):
        return chemin
    from urllib.parse import unquote, urlsplit
    decode = unquote(urlsplit(chemin).path)
    if os.name == "nt" and len(decode) > 2 and decode[0] == "/" and decode[2] == ":":
        decode = decode[1:]
    return decode or chemin


def _vise_data(chemin) -> bool:
    if chemin is None or isinstance(chemin, int):
        return False
    complet = _texte(os.fsdecode(_sans_uri(chemin))) + os.sep
    return complet.startswith(_etat["formes"])


def _audit(evenement: str, args: tuple) -> None:
    if evenement == "open":
        if not _etat["formes"]:
            return
        chemin, mode, drapeaux = args
        if not (any(c in (mode or "") for c in "wax+") or (drapeaux or 0) & _ECRIT):
            return
        chemins = (chemin,)
    elif evenement in _SURVEILLEES:
        if not _etat["formes"]:
            return
        chemins = tuple(args[i] for i in _SURVEILLEES[evenement] if i < len(args))
    else:
        return
    for chemin in chemins:
        if _vise_data(chemin):
            ecritures.append(f"{evenement} {os.fsdecode(chemin)}")
            raise EcritureDansData(f"{evenement} refuse sous data/ : {os.fsdecode(chemin)}")


def armer(donnees: os.PathLike) -> None:
    _etat["formes"] = _formes(os.fspath(donnees))
    if not _etat["installe"]:
        sys.addaudithook(_audit)
        _etat["installe"] = True


def desarmer() -> None:
    _etat["formes"] = ()
