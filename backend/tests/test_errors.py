from clipforge.errors import user_message


def test_known_errors_get_actionable_french_message():
    cases = [
        (RuntimeError("ERROR: [twitch:vod] Unable to download JSON metadata: getaddrinfo failed"),
         "connexion"),
        (RuntimeError("ffmpeg exited with code 1"), "ffmpeg"),
        (RuntimeError("Library cublas64_12.dll is not found"), "GPU"),
        (RuntimeError("Unable to allocate 2.51 GiB for an array"), "memoire"),
        (FileNotFoundError("fichier.mp4"), "introuvable"),
    ]
    for exc, expected_word in cases:
        msg = user_message(exc)
        assert expected_word.lower() in msg.lower()
        assert "Traceback" not in msg
        assert len(msg) <= 200


def test_the_container_and_lock_failures_reach_the_user_with_their_remedy():
    """Les deux messages soignes du chemin de telechargement tombaient dans le repli
    generique « Une erreur inattendue est survenue » : l'utilisateur ne lisait
    jamais le remede. Mesure sur le test reel du 2026-08-30."""
    conteneur = user_message(RuntimeError(
        "Durée indisponible : le conteneur n'est pas lisible"))
    assert "relance" in conteneur.lower()
    assert "inattendue" not in conteneur.lower()
    incomplet = user_message(RuntimeError(
        "Telechargement incomplet : v2836283171.mp4 n'est pas une video lisible"))
    assert "relance" in incomplet.lower()
    verrou = user_message(PermissionError(
        5, "Accès refusé", r"C:\data\downloads\v2836283171.temp.mp4"))
    assert "programme" in verrou.lower()
    assert "inattendue" not in verrou.lower()


def test_a_wrapped_repair_failure_keeps_its_own_remedy():
    """L'echec de reparation remonte ENVELOPPE dans « Telechargement incomplet ».
    Si la regle generique l'avalait, elle conseillerait de relancer une analyse
    qui echouerait exactement pareil."""
    verrou = user_message(RuntimeError(
        "Telechargement incomplet : v1.mp4 n'est pas une video lisible. "
        "Detail : v1.mp4 est verrouille par un autre programme"))
    assert "ouvert par un autre programme" in verrou.lower()
    abandon = user_message(RuntimeError(
        "Telechargement incomplet : v1.mp4 n'est pas une video lisible. "
        "Detail : Remux interrompu apres 30 min sans converger"))
    assert "reimporte" in abandon.lower()


def test_unknown_error_falls_back_without_leaking_trace():
    msg = user_message(ValueError("quelque chose d'inattendu"))
    assert "Traceback" not in msg
    assert len(msg) <= 200
    assert msg  # jamais vide


def test_specific_rules_win_over_the_generic_missing_file_rule():
    """Les outils absents ont leur message : la regle 'introuvable' ne doit pas les avaler."""
    chat = user_message(RuntimeError(
        r"TwitchDownloaderCLI introuvable : C:\t\cli.exe (variable TWITCHDOWNLOADER_CLI)"))
    assert "chat" in chat.lower()
    install = user_message(RuntimeError(
        "ffprobe introuvable - installer ffmpeg (winget install Gyan.FFmpeg)"))
    assert "winget" in install.lower()


def test_message_never_repeats_the_technical_text():
    """Contrat des deux niveaux : la technique reste dans error_detail, jamais dans error."""
    msg = user_message(RuntimeError(
        r"Library cublas64_12.dll is not found in C:\Users\Camille\prive"))
    assert "cublas64_12.dll" not in msg
    assert "Camille" not in msg


def test_a_twitch_vod_id_containing_404_is_not_read_as_an_http_error():
    """Un identifiant de VOD n'est pas un code HTTP : pas de correspondance sur des chiffres nus."""
    msg = user_message(RuntimeError("Fichier telecharge introuvable : v404871.mp4"))
    assert "introuvable" in msg.lower()
    assert "twitch" not in msg.lower()


def test_a_saturated_gpu_asks_to_free_memory_not_to_check_drivers():
    """'CUDA out of memory' parle des deux sujets : le remede utile est la memoire."""
    msg = user_message(RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"))
    assert "memoire" in msg.lower()
    assert "pilotes" not in msg.lower()


def test_a_real_driver_failure_still_points_at_the_gpu():
    """Contre-epreuve : sans mot de memoire, l'erreur CUDA reste un probleme de GPU."""
    msg = user_message(RuntimeError("CUDA error: no kernel image is available for execution"))
    assert "gpu" in msg.lower()


def test_a_ctranslate2_allocation_failure_reads_as_a_memory_problem():
    """Message exact leve par ctranslate2.dll quand mkl_malloc rend NULL.

    Aucune des formulations deja couvertes ne le reconnait (« failed to
    allocate », ni « unable » ni « out of memory ») : il tombait dans le
    fallback « erreur inattendue », qui n'aide personne a liberer de la RAM.
    """
    msg = user_message(RuntimeError("mkl_malloc: failed to allocate memory"))
    assert "memoire" in msg.lower()
    assert "inattendue" not in msg.lower()


def test_refused_download_and_full_disk_have_their_own_message():
    refus = user_message(RuntimeError("ERROR: unable to download video data: HTTP Error 403"))
    assert "twitch" in refus.lower()
    disque = user_message(OSError("[Errno 28] No space left on device"))
    assert "disque" in disque.lower()


def test_a_set_aside_source_tells_the_user_the_download_will_heal_it():
    """D8/D9 : « Remux tronqué (1620 s pour 17170 s attendues) » tombait dans le
    repli « Une erreur inattendue est survenue », alors que le pipeline venait
    justement d'ecarter le fichier et de rendre le retelechargement possible."""
    msg = user_message(RuntimeError(
        "source ecartee sous v2836283171.corrompu.mp4 apres : Remux de "
        "v2836283171.mp4 tronque (1620 s pour 17170 s attendues), source conservee"))
    assert msg == ("Source endommagée — fichier mis de côté ; relance l'analyse "
                   "pour retélécharger la VOD.")


def test_a_remux_failure_is_never_an_unexpected_error():
    """D9 : la famille entiere doit atterrir sur un remede, pas sur le repli.

    Le message ne promet PAS de retelechargement : ces echecs-la sont ceux d'une
    source qu'on ne sait pas remplacer toute seule (import local, ffmpeg qui
    refuse l'entree).
    """
    for detail in ("Remux de v1.mp4 tronque (1620 s pour 17170 s attendues), source conservee",
                   "Remux de v1.mp4 toujours illisible, source conservee : Duree indisponible",
                   "Remux de v1.mp4 sans sortie, source conservee",
                   "Remux echoue : Invalid frame dimensions 0x0"):
        msg = user_message(RuntimeError(detail))
        assert "inattendue" not in msg.lower(), detail
        assert "reimporte" in msg.lower(), detail
        assert "1620" not in msg and "v1.mp4" not in msg      # contrat des deux niveaux
