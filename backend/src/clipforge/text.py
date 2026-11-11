"""Assemblage des mots de transcription en texte lisible.

Site UNIQUE de la jonction : partout ou des mots issus de la table `words` sont
recolles en phrase (accroche d'un segment, transcription envoyee a l'IA,
paquets de sous-titres), c'est `join_words` qui decide de l'espace.

faster-whisper decoupe l'elision et le trait d'union en mots separes — « c » +
« 'est », « allez » + « -vous » (3969 mots sur 47798 commencent par une
apostrophe sur la VOD 1, mesure du 2026-09-18). Un `" ".join` naif ecrivait
donc « C 'EST PAS MOI » sur la bande et « c est » dans les noms de fichier. La
regle est portee ici, jamais recopiee : un mot qui commence par une apostrophe
ou un trait d'union appartient au mot precedent.

Ce module ne connait ni la base ni le rendu, et ne modifie jamais les mots
stockes : il ne repond qu'a « comment ces mots s'ecrivent-ils cote a cote ».
"""
from collections.abc import Iterable

CONTINUATION_PREFIXES = ("'", "’", "-")


def is_continuation(text: str) -> bool:
    """Vrai si ce mot est la suite du precedent et doit s'y coller sans espace.

    L'apostrophe droite et la typographique comptent toutes deux : la
    transcription rend la premiere, un mot retouche a la main peut porter la
    seconde."""
    return text.strip().startswith(CONTINUATION_PREFIXES)


def join_words(texts: Iterable[str]) -> str:
    """Recolle des mots en texte : une espace entre eux, aucune avant une
    continuation.

    Les blancs de bordure tombent et les mots vides sont ignores — sans quoi un
    mot retouche dans l'editeur laisserait un double espace ou ferait echouer le
    collage."""
    out = ""
    for raw in texts:
        text = raw.strip()
        if not text:
            continue
        if out and not is_continuation(text):
            out += " "
        out += text
    return out
