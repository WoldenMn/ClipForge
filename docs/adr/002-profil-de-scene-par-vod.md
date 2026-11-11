# 002. Profil de scène : celui de la VOD d'abord, puis le réglage global

- Date : 2026-10-30
- Statut : acceptée

## Contexte

Le mode automatique (V5) fige un profil de scène dans chaque clip qu'il fabrique. Jusqu'ici, ce profil était l'unique profil de la base, et deux profils bloquaient l'étape `making_clips` sur un message qui demandait d'en tracer un, ce qui était faux.

Le pont OBS (`scripts/Importer-Enregistrement-OBS.ps1`) apporte des enregistrements 2560×1440 cadrés par la scène « Jeux » d'OBS, à côté des VOD Twitch déjà cadrées par un profil 1920×1080 (« Plateau GoR »). Un profil est un rectangle en pixels de la source : celui d'OBS déborde d'une VOD 1080p, et `validate_profile_against_source` refuse alors le rendu.

## Options

| Option | Pour | Contre |
|---|---|---|
| Réglage global seul, posé par le pont | une seule notion | chaque envoi OBS change le cadrage des VOD Twitch, dont le rendu casse sur une source 1080p |
| Profils exprimés en proportions de l'image | indépendants de la résolution | migration des coordonnées, éditeur et aperçu à refaire, sans régler le cas de deux mises en page différentes |
| Profil porté par la VOD, puis réglage global | chaque source garde son cadrage ; rien ne change pour les VOD existantes | une colonne et un paramètre de plus |

## Décision

- `vods.profile_id` (migration 8, `ON DELETE SET NULL`) porte le profil propre à une VOD. `POST /api/vods/{id}/analyze?profile=<nom>` le pose ; une relance sans le paramètre le garde ; un nom inconnu rend 400 sans mettre la VOD en file.
- `db.clip_profile_id` choisit le profil d'un clip : celui de la VOD, sinon `auto_profile_id` (le réglage `auto_profile`, sinon l'unique profil). `making_clips`, `step_autoclips` et `POST /api/clips` passent tous par lui.
- Le réglage `auto_profile` se choisit dans Réglages, « Profil des clips automatiques ». ClipForge ne le pose jamais : deux profils sans choix restent un refus explicite, qui dit maintenant où choisir.
- Le pont, quand il crée son profil à côté d'un profil unique et d'un réglage vide, fige ce profil unique dans `auto_profile` et le dit : les VOD Twitch gardent le cadrage qu'elles avaient, sans geste du user.
- Le pont nomme son profil par la hauteur de l'enregistrement (« WOLDENM OBS 1440p ») et ne le crée que s'il manque : un profil retouché dans l'éditeur n'est jamais réécrit.

## Conséquences

- Une base existante passe en version 8 sans rien perdre : chaque VOD a `profile_id` à NULL et suit le réglage, comme avant.
- Le profil d'une VOD supprimé, elle retombe sur le réglage global au lieu d'être cadrée par un autre profil choisi en silence.
- Les recadrages du pont visent la mise en page de clip de `render/ffmpeg_cmd.py` (webcam 1080×576, jeu 1080×1344) : webcam rognée de 10 px en haut et en bas et de 2 px sur les côtés pour sortir des coins arrondis de 14 px, jeu recadré au centre en 1158×1440.
