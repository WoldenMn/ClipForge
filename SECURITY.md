# Sécurité

ClipForge tourne en local sous Windows. Son moteur, un serveur FastAPI, écoute
sur `127.0.0.1:8765` et n'accepte que les hôtes et origines locaux. Les sorties
réseau servent ce que l'utilisateur demande : télécharger une VOD et son chat
replay, et, si `ANTHROPIC_API_KEY` est posée dans l'environnement, le scoring
IA des segments. Sans cette clé, le classement reste entièrement local.

## Signaler une faille

Ne pas ouvrir de ticket public. Passer par le signalement privé de GitHub :
onglet **Security** du dépôt, puis **Report a vulnerability**
(<https://github.com/WoldenMn/ClipForge/security/advisories/new>).

Décrire la version de ClipForge, la version de Windows, les étapes pour
reproduire et l'effet obtenu. Ne jamais joindre de clé API.

## Versions prises en charge

Seule la dernière version publiée reçoit des correctifs de sécurité.

## Ce que le modèle de menace couvre

- Une page web ouverte dans le navigateur qui viserait le moteur local :
  hôtes et origines filtrés, écoute sur la boucle locale seulement.
- Une VOD ou un fichier de chat piégé : traités par ffmpeg et le pipeline
  comme des données, jamais exécutés.
- La clé API : lue dans l'environnement, jamais écrite dans le dépôt ni dans
  les journaux.

Hors du modèle : une machine déjà compromise et un attaquant avec un accès
physique.
