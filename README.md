# ClipForge

ClipForge transforme une VOD de stream (Twitch ou enregistrement OBS) en clips verticaux courts et sous-titrés. Le pipeline analyse l'audio, le chat replay et la transcription pour repérer les moments forts, puis un scoring IA optionnel affine le classement. L'app tourne entièrement en local sur Windows, sans compte ni service cloud requis pour l'usage courant.

## Prérequis

| Outil | Rôle | Installation |
|---|---|---|
| Python 3.12+ et [uv](https://docs.astral.sh/uv/) | Backend FastAPI | `winget install astral-sh.uv` (ou voir la doc uv) |
| [Bun](https://bun.sh/) | Build du frontend | voir bun.sh |
| FFmpeg 7.0 ou plus (build complet, filtre `subtitles`) | Extraction audio, rendu vertical, sous-titres ASS | `winget install Gyan.FFmpeg` |
| GPU NVIDIA (CUDA, NVENC) | Transcription et rendu accélérés | pilotes NVIDIA à jour ; repli CPU automatique si absent |
| TwitchDownloaderCLI (optionnel) | Téléchargement du chat replay | [releases GitHub](https://github.com/lay295/TwitchDownloader/releases), zip Windows-x64 décompressé dans `C:\tools\TwitchDownloaderCLI\` |

Sans TwitchDownloaderCLI, tout fonctionne sauf le signal de vélocité du chat : le classement se base alors sur l'audio et la densité de parole.

FFmpeg 7.0 est un plancher : le montage V6 passe son graphe de filtres à FFmpeg par un fichier (`-/filter_complex`), option apparue avec la 7.0. Le rapport de diagnostic signale une version plus ancienne, ou qu'il ne sait pas lire, à la ligne `ffmpeg_montage_v6`.

## Installation

```powershell
cd backend
uv sync

cd ..\frontend
bun install
bun run build
```

## Lancement

Application native. Construire l'exécutable une fois :

```powershell
cd src-tauri
cargo tauri build --no-bundle
```

Puis double-cliquer sur `src-tauri\target\release\clipforge.exe`, depuis n'importe quel dossier — ou créer un raccourci vers ce fichier sur le Bureau. Un écran de démarrage s'affiche, l'app lance le moteur Python, attend sa réponse sur `/api/health`, puis ouvre l'interface. Fermer la fenêtre arrête le moteur et toute sa descendance (uvicorn, Python, ffmpeg).

Deux points à connaître :

- `frontend\dist` doit exister, l'app native ne le construit pas (voir Installation).
- Le dossier `backend` est trouvé en remontant depuis l'exécutable. Pour une disposition inhabituelle, poser `CLIPFORGE_BACKEND_DIR` sur le dossier qui contient `pyproject.toml`.

## Installeur MSI

```powershell
scripts\Construire-Msi.ps1
```

Produit `src-tauri\target\release\bundle\msi\ClipForge_<version>_x64_en-US.msi` (~3,5 Mo, `<version>` celle de `src-tauri/tauri.conf.json`), qui installe `clipforge.exe` dans `C:\Program Files\ClipForge` avec un raccourci dans le menu Démarrer.

**Le MSI n'embarque que l'exécutable natif.** Conformément à la spec V3, Tauri pilote un moteur Python installé localement : ni `backend`, ni `backend\.venv`, ni `frontend\dist`, ni le modèle Whisper ne sont empaquetés. L'app installée doit donc retrouver le dossier du projet, et elle vit hors de son arborescence.

Chaîne de résolution, dans l'ordre (`src-tauri\src\backend_path.rs`) :

| Rang | Source | Cas couvert |
|---|---|---|
| 1 | `CLIPFORGE_BACKEND_DIR` | échappatoire manuelle, prioritaire |
| 2 | remontée depuis l'exécutable | binaire lancé depuis le projet |
| 3 | `%APPDATA%\com.clipforge.app\backend-dir.txt` | app installée, hors du projet |
| 4 | ressources embarquées | futur empaquetage autonome |

Le rang 2 **écrit** le fichier du rang 3 à chaque fois qu'il aboutit. Concrètement : lancer une fois `src-tauri\target\release\clipforge.exe` depuis le projet suffit à ce que la copie installée trouve le moteur. Comme le rang 2 passe avant le rang 3, déplacer le dossier du projet puis relancer le binaire local corrige le pointeur au lieu de le laisser mentir. À défaut, écrire le chemin du dossier `backend` à la main dans ce fichier fait la même chose — le message d'erreur au démarrage donne le chemin exact.

`scripts\Construire-Msi.ps1` existe parce que `cargo tauri build` seul échoue sur un Windows dépourvu du moteur VBScript : la validation ICE de WiX renvoie l'erreur 2738 et Tauri n'affiche qu'un « failed to run light.exe » qui masque la cause. Le MSI est déjà correct à ce stade ; le script relinke avec `-sval`, qui saute cette validation facultative.

Méthode alternative, sans interface native (serveur + navigateur) :

```powershell
scripts\Lancer-ClipForge.ps1
```

Le script construit le frontend si `frontend\dist` est absent, démarre le serveur sur `http://127.0.0.1:8765` et ouvre le navigateur.

## Pont OBS

`scripts\Importer-Enregistrement-OBS.cmd` (double-clic, fiche avant lancement) envoie le dernier enregistrement d'OBS à ClipForge et lance l'analyse :

1. prend le fichier le plus récent de `D:\WOLDENM\Enregistrements` (les clips du tampon de relecture, préfixe « Clip », sont écartés) et refuse un fichier qu'OBS écrit encore ;
2. réveille ClipForge s'il dort, inscrit le fichier ou reprend la VOD déjà inscrite ;
3. cadre ses clips par le profil « WOLDENM OBS <hauteur>p », tiré de la scène « Jeux » d'OBS et créé s'il manque ; un profil retouché dans l'éditeur n'est jamais réécrit ; s'il rejoint un profil unique, celui-ci est figé comme « Profil des clips automatiques » pour que les autres sources gardent leur cadrage ;
4. lance l'analyse avec ce profil pour cette VOD seule : le réglage « Profil des clips automatiques » reste celui des autres sources ([ADR 002](docs/adr/002-profil-de-scene-par-vod.md)).

Codes de sortie : 0 fait, 1 erreur, 2 rien à faire ou refus (dossier vide, OBS écrit encore, analyse déjà en cours).

## Variables d'environnement (`.env`)

| Variable | Rôle | Défaut |
|---|---|---|
| `ANTHROPIC_API_KEY` | Active le scoring IA des segments (titres, hashtags, score affiné). Optionnelle — sans elle, le classement reste purement local (audio + chat + parole). | vide |
| `TWITCHDOWNLOADER_CLI` | Chemin vers `TwitchDownloaderCLI.exe` | `C:\tools\TwitchDownloaderCLI\TwitchDownloaderCLI.exe` |
| `CLIPFORGE_WHISPER_MODEL` | Optionnelle — si posée, force le modèle faster-whisper et IGNORE le réglage du panneau Réglages | `large-v3` |
| `CLIPFORGE_DATA` | Dossier de données runtime (DB, téléchargements, jobs, exports) | `./data` |
| `CLIPFORGE_GPU_INDEX` | Index CUDA de la carte qui porte Whisper (`device_index` de faster-whisper, seul moment où la carte se choisit). Sur une machine à deux cartes, `0` et `1` suivent l'ordre CUDA : donner à Whisper la plus rapide. Valeur non entière = arrêt du moteur | `0` |
| `CLIPFORGE_ALIGNEUR` | Lanceur du Python de WhisperX qui recale les mots de faster-whisper (débuts 100 à 120 ms trop tôt sinon, [ADR 001](docs/adr/001-horodatage-des-mots.md)). `0` coupe le recalage ; lanceur absent ou en échec = temps de faster-whisper, dit dans le journal | `C:\tools\whisperx\python-local.cmd` |

Copier `.env.example` vers `.env` et renseigner les valeurs nécessaires.

## Architecture

```
ClipForge/
├── backend/src/clipforge/   # FastAPI (app, config, db, gpu)
│   ├── api/                 # routes REST (vods, segments, clips, profiles, settings)
│   ├── pipeline/            # probe, download, chat, transcription, analyse, scoring IA
│   └── render/               # sous-titres ASS, commande ffmpeg, file de rendu
├── backend/aligneur/         # recalage des mots, lancé dans le venv de WhisperX (ADR 001)
├── frontend/src/             # Vite + React 19 + Tailwind (Accueil, Candidats, Éditeur, Export)
├── src-tauri/                # app native Windows (lance le moteur, écran de démarrage)
├── data/                     # SQLite, downloads, jobs, output — non versionné
├── scripts/Lancer-ClipForge.ps1         # launcher (build + serveur + navigateur)
├── scripts/Construire-Msi.ps1     # construction de l'installeur MSI
├── scripts/Importer-Enregistrement-OBS.ps1  # pont OBS (lanceur .cmd à côté)
├── scripts/generer_icones.py # icônes Tauri, .ico et favicon depuis assets/ (uv run)
└── assets/                   # icon.svg (48 px et plus), icon-petit.svg (16 à 44 px)
```

## Limitations V1

- **Sources Twitch et fichiers locaux uniquement** (pas de Kick / YouTube Live).
- **Pas de publication automatique** : les clips exportés doivent être publiés manuellement sur TikTok, YouTube Shorts ou Instagram Reels.
- **Chat replay optionnel** : sans TwitchDownloaderCLI, le signal de vélocité du chat est simplement absent du scoring.
- **Détection de visage et multi-scènes** : hors périmètre, le profil de scène (webcam / jeu) est tracé manuellement dans l'éditeur, sauf pour un enregistrement envoyé par le pont OBS, cadré d'après la scène « Jeux ».

## Arrêt du serveur

Un `Ctrl+C` laisse les jobs en cours (analyse ou rendu) aller jusqu'au bout — seuls les jobs en attente dans la file sont annulés. Un second `Ctrl+C` force l'arrêt immédiat. Le pipeline étant reprennable (chaque étape persiste son résultat en base), interrompre un job en cours est sans danger : au prochain lancement, il reprend là où il s'est arrêté plutôt que de tout refaire.
