# Changelog

Les modifications notables de ClipForge sont notées ici, au format Keep a
Changelog, et les versions suivent le versionnage sémantique.

## [1.0.0] - 2026-11-11

Première version publique. ClipForge transforme une VOD de stream, Twitch ou
enregistrement OBS, en clips verticaux courts et sous-titrés, entièrement en
local sous Windows.

### Ajouté

- Repérage des moments forts par l'audio, le chat replay et la transcription,
  avec un classement par IA en option.
- Transcription par faster-whisper sur carte graphique, mots recalés par
  l'aligneur de WhisperX quand il est installé.
- Rendu vertical par FFmpeg et NVENC, sous-titres animés, zones sûres des
  réseaux, son normalisé à −14 LUFS.
- Montage automatique : coupes sur les silences, accroche en ouverture, zooms
  sur les temps forts.
- Mode automatique d'une adresse de VOD jusqu'à cinq clips prêts à poster.
- Application native Tauri et installeur MSI.
