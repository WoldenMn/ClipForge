# 001. Horodatage des mots : faster-whisper transcrit, l'aligneur de WhisperX recale

- Date : 2026-10-30
- Statut : acceptée

## Contexte

Les mots horodatés servent trois fois dans ClipForge : les bornes de clips (`segmenter.py`, phrases coupées sur une pause de 0,8 s), les coupes du montage V6 (`render/montage/edl.py`, zone coupée entre la fin d'un mot et le début du suivant, marge `PAD_S` de 0,12 s de chaque côté) et les sous-titres mot à mot (`captions_pop.py`). Un début trop tôt laisse du silence ; une fin trop tôt fait mordre la coupe dans la fin du mot ; une fin trop tard fait disparaître la coupe.

Le banc retouche vidéo du 2026-10-29 avait mesuré faster-whisper 97 à 120 ms trop tôt sur les débuts de mots, et retenu WhisperX 3.8.6 avec l'alignement français libre `jonatasgrosman/wav2vec2-large-xlsr-53-french` (Apache-2.0), installé dans `C:\tools\whisperx`. Restait à savoir si ce gain tient dans ClipForge, sur ses propres usages.

## Banc

Mené le 2026-10-30 sur la RTX 3060, une charge à la fois (`lancer_lourd.py`), par le chemin de production de ClipForge (`transcribe_to_db`, large-v3, float16, faisceau 5, VAD) et le lanceur WhisperX du poste (lot 8). Deux pièces :

- **Vérité SAPI** à deux voix (Paul, Hortense), 203 jetons, débuts de mots lus sur l'événement `SpeakProgress` ; fins de parole détectées sur l'énergie de la voix seule.
- **Rush réel** : 600 s de `data/downloads/v2809796119.mp4` à partir de 6000 s, plusieurs voix et un jeu en fond ; la voix isolée par audio-separator (MelBand Roformer) sert de référence d'énergie.

Les coupes sont celles de `build_cuts` lui-même, style `standard`, sur des fenêtres de 30 s (vérité) ou 60 s (rush). Une coupe « mord » quand il reste au moins 20 ms de voix dans les 150 ms de son bord.

Instruments contrôlés avant usage : témoin VRAM vide à 0 Mio ; détection des reprises de parole à −23 à −29 ms de la vérité, et un décalage de +300 ms relu à +300 ms. Sur le rush, le même témoin ne se relit qu'à +190 et −160 ms (paroles qui se chevauchent) : la dispersion des débuts s'y juge mal, elle se juge sur la vérité seule.

### Débuts de mots, vérité SAPI

| Voix | Mesure | faster-whisper | WhisperX | Hybride |
|---|---|---|---|---|
| Paul | biais médian | −97 ms | +78 ms | +81,5 ms |
| Paul | dispersion p90 | 140 ms | 30 ms | 33 ms |
| Paul | erreur de mots | 4,9 % | 5,9 % | 4,9 % |
| Hortense | biais médian | −120 ms | +67 ms | +71 ms |
| Hortense | dispersion p90 | 130 ms | 42 ms | 47 ms |
| Hortense | erreur de mots | 6,4 % | 4,4 % | 6,4 % |

L'attaque réelle de la voix SAPI suit `SpeakProgress` de 20 à 26 ms : l'hybride tombe donc 45 à 55 ms après l'attaque, que la marge `PAD_S` de 0,12 s couvre.

### Fins de phrase et coupes, vérité SAPI (fenêtres de 30 s)

| Voix | Mesure | faster-whisper | WhisperX | Hybride |
|---|---|---|---|---|
| Paul | fin du mot avant une pause | −150 ms | +826 ms | +110 ms |
| Paul | coupes, silence retiré | 8, 3,5 s | 0 | 11, 6,3 s |
| Paul | coupes qui mordent un mot | 6 (540 ms) | 0 | 1 (150 ms) |
| Hortense | fin du mot avant une pause | −220 ms | +910 ms | +10 ms |
| Hortense | coupes, silence retiré | 10, 4,9 s | 2, 1,3 s | 11, 8,3 s |
| Hortense | coupes qui mordent un mot | 8 (870 ms) | 1 (150 ms) | 1 (150 ms) |

WhisperX étire la fin du dernier mot de chaque segment sur toute la pause qui suit (« vidéo. » de 3,85 à 4,99 s quand la voix s'arrête à 4,23 s) : le dernier caractère aligné garde les trames de blanc jusqu'au bout de la fenêtre. Lire la fin de la dernière lettre (`--return_char_alignments`) n'y change presque rien (+556 et +759 ms).

### Rush réel (voix isolée, seuil −40 dB, fenêtres de 60 s)

| Mesure | faster-whisper | WhisperX | Hybride |
|---|---|---|---|
| mots | 2 398 | 2 031 | 2 398, texte identique |
| jetons de faster-whisper absents de l'autre | — | 159, dont des phrases entières (« est-ce qu'il y a un train à 10h17 oui je demande oui ») | 0 |
| biais des reprises de parole | −80 ms | +9 ms | +8,5 ms |
| fin du mot avant une pause | +20 ms | +515 ms | +135 ms |
| coupes, silence retiré | 63, 59,8 s | 30, 34,0 s | 59, 60,5 s |
| coupes qui mordent un mot | 44 | 19 | 33 |

Le résultat ne dépend pas du seuil : à −45 et −35 dB, le classement reste le même.

### Marge après segment de l'hybride

Chaque segment s'aligne dans une fenêtre élargie de 0,3 s avant et d'une marge après ; l'aligneur étire le dernier mot jusqu'au bout de cette marge, d'où son réglage.

| Marge après | Paul : coupes, mordent | Hortense : coupes, mordent | Rush : coupes, silence, mordent |
|---|---|---|---|
| 0 s | 11, 8 | 12, 10 | 86, 87,6 s, 61 |
| 0,15 s | 11, 2 | 11, 5 | 73, 74,3 s, 48 |
| **0,3 s** | **11, 1** | **11, 1** | **59, 60,6 s, 33** |
| 0,5 s | 6, 1 | 11, 1 | 49, 48,3 s, 26 |

À 0,3 s, la vérité ne mord plus qu'une fois par voix et le rush garde le silence retiré de faster-whisper (60,6 s contre 59,8 s). À 0,5 s, les coupes de Paul tombent de 11 à 6.

### Coût, rush de 10 min sur la RTX 3060

| Moteur | Temps | VRAM de crête |
|---|---|---|
| faster-whisper, une tranche | 89,1 s | 4,4 Go |
| faster-whisper, deux tranches parallèles | 99,9 s | 5,4 Go |
| WhisperX | 60,0 s | 7,7 Go |
| Hybride : faster-whisper puis aligneur | 89,1 + 25,7 s | 4,4 Go (aligneur seul : 2,1 Go, modèle libéré avant) |

Le banc du 2026-10-29 donnait 346 s à faster-whisper et 191 s à WhisperX sur le même rush ; ces temps ne se reproduisent pas aujourd'hui, mêmes réglages et même carte. Cause non vérifiée (poste chargé ce jour-là, probablement) : seuls les temps du 2026-10-30 servent ici.

## Options

| Option | Pour | Contre |
|---|---|---|
| A. Garder faster-whisper seul | rien à installer ; texte le plus complet | débuts 100 à 120 ms trop tôt, dispersion 130 à 140 ms ; fins de phrase trop tôt : 6 à 8 coupes sur 8 à 10 mordent la fin d'un mot sur la vérité |
| B. Passer à WhisperX complet | débuts justes (dispersion 30 à 42 ms) ; 1,5 fois plus rapide | perd 7 % des mots du rush, phrases entières ; fins étirées sur les pauses : moitié moins de coupes, phrases du segmenteur fondues ; 7,7 Go de VRAM |
| C. Hybride : faster-whisper transcrit, l'aligneur de WhisperX recale | texte de faster-whisper intact ; débuts justes ; une coupe qui mord par voix au lieu de 6 et 8, 25 % de morsures en moins sur le rush à silence retiré égal ; crête de VRAM inchangée | +29 % de temps de transcription ; dépend d'un venv hors du dépôt (`C:\tools\whisperx`) |

## Décision

Option C, marge après segment de 0,3 s. Elle seule garde le meilleur texte (celui de faster-whisper) et la meilleure précision (celle de l'aligneur) ; WhisperX complet perd des phrases et casse les coupes, ce qu'aucun gain de vitesse ne rachète pour un outil de montage.

## Conséquences

- `pipeline/align.py` recale les mots après la transcription, une fois le modèle faster-whisper libéré, par un sous-processus : le lanceur `CLIPFORGE_ALIGNEUR` (défaut `C:\tools\whisperx\python-local.cmd`, qui pose les variables du lanceur WhisperX du poste) exécute `backend/aligneur/aligner_mots.py` dans le venv de WhisperX. ClipForge n'embarque ni torch ni WhisperX.
- Repli : lanceur absent, `CLIPFORGE_ALIGNEUR=0`, code de sortie non nul, délai dépassé (180 s + 0,2 s par seconde d'audio), sortie illisible : les temps de faster-whisper restent et le journal le dit. Un segment rendu avec un autre nombre de mots, ou une paire hors de sa fenêtre, garde ses temps d'origine.
- La carte suit `CLIPFORGE_GPU_INDEX`, comme faster-whisper ; le bail gpu-queue de l'étape couvre aussi l'alignement.
- Les débuts recalés restent 45 à 55 ms après l'attaque réelle, sans correction : la marge `PAD_S` de 0,12 s les couvre. Retrancher ce biais n'a pas été mesuré sur les sous-titres.
- Une VOD déjà transcrite (`words_complete=1`) garde ses temps : le recalage ne joue qu'à la prochaine transcription.
- Pour rejouer le banc : l'atelier de mesure du 2026-10-30, gardé hors du dépôt (outils `cf_mots.py`, `aligner_fw.py`, `analyse.py`, `serie*.sh` ; mesures brutes dans `mesures\`).
