import re

_RULES: list[tuple[str, str]] = [
    # EN TETE, avant meme le reseau : la mise de cote est une decision DEJA PRISE
    # par le pipeline (fichier renomme, source_path vide), pas un diagnostic a
    # arbitrer. Le detail technique qu'elle enveloppe porte la phrase de l'echec
    # d'origine — « Remux interrompu », « tronque » — et n'importe quelle regle
    # placee avant celle-ci la remplacerait par un remede perime.
    (r"source ecartee",
     ("Source endommagée — fichier mis de côté ; relance l'analyse pour "
      "retélécharger la VOD.")),
    ((r"getaddrinfo failed|connection (refused|reset|aborted)|max retries exceeded"
      r"|timed? ?out|network is unreachable"),
     "Probleme de connexion reseau. Verifie ta connexion puis relance l'analyse."),
    (r"ffmpeg introuvable|ffprobe introuvable",
     "ffmpeg n'est pas installe ou absent du PATH (winget install Gyan.FFmpeg)."),
    (r"twitchdownloadercli introuvable",
     "TwitchDownloaderCLI est introuvable : l'analyse du chat sera ignoree."),
    (r"unable to download|http error 4\d\d|forbidden",
     "Twitch a refuse le telechargement (VOD privee, supprimee ou trop recente)."),
    # Les trois cas du chemin de telechargement, avant la regle ffmpeg : chacun
    # porte deja son remede, et il serait perdu dans « le fichier est peut-etre
    # incomplet ». Sans eux ils tombaient tous dans le repli generique.
    #
    # Leur ordre interne compte doublement : l'echec de reparation remonte
    # ENVELOPPE dans le message « Telechargement incomplet », donc le verrou et
    # l'abandon doivent passer les premiers. Sinon la regle generique les avale
    # et conseille de relancer une analyse qui echouerait pareil.
    ((r"verrouille par un autre programme|permissionerror|acces refuse|accès refusé"
      r"|access is denied|being used by another process"),
     ("Le fichier est ouvert par un autre programme. Ferme le lecteur video "
      "(ou l'explorateur qui l'affiche) puis relance l'analyse.")),
    # Toute la famille du remux, et pas seulement l'abandon sur delai : le
    # controle d'integrite ajoute par le correctif 5cadfe8 rendait
    # « Remux de v2836283171.mp4 tronque (1620 s pour 17170 s attendues) », que
    # l'utilisateur lisait « Une erreur inattendue est survenue » alors que le
    # diagnostic utile existait deja. Ce remede-ci ne promet PAS de
    # retelechargement : quand ClipForge peut en obtenir un, la regle « source
    # ecartee » a deja repondu plus haut.
    (r"remux interrompu|remux echoue|remux de .+ (tronque|sans sortie|toujours)",
     ("La reparation de la video n'a pas abouti : le fichier source est "
      "endommage. Supprime la VOD puis reimporte-la pour repartir d'une source "
      "saine.")),
    (r"telechargement incomplet|duree indisponible|durée indisponible",
     ("Le fichier video n'est pas exploitable (conteneur brut ou telechargement "
      "interrompu). Relance l'analyse : ClipForge le reparera avant de le relire.")),
    (r"ffmpeg exited|ffmpeg a echoue|ffprobe a echoue|invalid data found|moov atom not found",
     "Le traitement video a echoue (ffmpeg). Le fichier est peut-etre incomplet."),
    # Avant la regle GPU : "CUDA out of memory" parle des deux, mais le remede
    # utile est de liberer de la memoire, pas de toucher aux pilotes.
    # « failed to allocate » couvre mkl_malloc, l'allocateur CPU de ctranslate2
    (r"unable to allocate|out of memory|memoryerror|cannot allocate|failed to allocate",
     "Memoire insuffisante pour traiter cette VOD. Ferme les autres applications."),
    (r"cublas|cudnn|cuda|nvrtc|no kernel image",
     "Probleme de GPU (CUDA ou pilotes NVIDIA). Verifie les pilotes puis relance l'analyse."),
    (r"no space left|disk full|espace disque",
     "Espace disque insuffisant : libere de la place puis relance."),
    (r"no such file|introuvable|filenotfound",
     "Fichier introuvable. Il a peut-etre ete deplace ou supprime."),
]

_FALLBACK = "Une erreur inattendue est survenue. Le detail technique est disponible ci-dessous."


def user_message(exc: BaseException) -> str:
    """Message court, francais et actionnable pour l'utilisateur final.

    Le detail technique (traceback, chemins, noms de DLL) vit dans une colonne
    separee : la valeur renvoyee ici est toujours une constante, jamais un
    fragment de l'exception, pour qu'aucune pile d'appels ne puisse fuir dans
    l'interface. L'ordre des regles compte : les cas precis (outil absent) sont
    testes avant les cas generiques (fichier introuvable).
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    for pattern, message in _RULES:
        if re.search(pattern, text):
            return message
    return _FALLBACK
