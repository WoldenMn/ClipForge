from unittest.mock import MagicMock, patch

from clipforge import db as dbmod
from clipforge.pipeline.ai_writing import ClipWriting, WritingResult, write_clips


def _seed(conn, *, n=1, hook_text="mot un mot deux mot trois"):
    """Un VOD avec n segments (hook_text fourni) + leurs clips, mots dans chaque fenetre."""
    vod_id = dbmod.create_vod(conn, title="t", source_path="x.mp4")
    clip_ids = []
    for i in range(n):
        start_s, end_s = 10.0 + i * 100, 40.0 + i * 100
        cur = conn.execute(
            "INSERT INTO segments (vod_id, start_s, end_s, score, hook_text) VALUES (?,?,?,?,?)",
            (vod_id, start_s, end_s, 0.9, hook_text))
        seg_id = cur.lastrowid
        for j, t in enumerate([start_s + 1.0, start_s + 2.0]):
            conn.execute("INSERT INTO words VALUES (?,?,?,?,?,?)",
                         (vod_id, i * 10 + j, t, t + 0.4, f"mot{j}", 0.9))
        cur = conn.execute(
            "INSERT INTO clips (segment_id, vod_id, start_s, end_s) VALUES (?,?,?,?)",
            (seg_id, vod_id, start_s, end_s))
        clip_ids.append(cur.lastrowid)
    conn.commit()
    return vod_id, clip_ids


def test_no_api_key_falls_back_to_hook_text(conn, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    vod_id, clip_ids = _seed(conn, hook_text="Le moment le plus dingue de la soiree")
    ok = write_clips(conn, vod_id, clip_ids)
    assert ok is False
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    assert row["hook"] == "Le moment le plus dingue de la soiree"
    assert row["title"]  # jamais vide
    assert row["emoji"] is None


def test_no_api_key_and_no_hook_text_leaves_hook_and_emoji_null(conn, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    vod_id, clip_ids = _seed(conn, hook_text=None)
    ok = write_clips(conn, vod_id, clip_ids)
    assert ok is False
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    assert row["hook"] is None
    assert row["emoji"] is None
    assert row["title"]  # replie sur un nom generique


def test_fallback_hook_with_no_space_is_hard_clamped_to_40_chars(conn, monkeypatch):
    """D3 : le plafond du repli est desormais 40 (aligne sur la consigne IA, D5).
    Sans espace disponible, aucune coupe sur mot n'est possible : coupe dure."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    long_hook = "x" * 80
    vod_id, clip_ids = _seed(conn, hook_text=long_hook)
    write_clips(conn, vod_id, clip_ids)
    row = conn.execute("SELECT hook FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    assert len(row["hook"]) == 40 and row["hook"] == "x" * 40


def test_fallback_hook_truncates_on_last_space_never_mid_word(conn, monkeypatch):
    """Cas realiste (Task 10, D3) : un fragment de transcription de 60+ caracteres
    coupe naivement a 40 tomberait en plein mot ("possibilit" au lieu de
    "possibilites"). Le repli doit reculer jusqu'au dernier espace."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    hook_text = "Apres, il y a encore des possibilites. Mais enfin, mais... M"
    vod_id, clip_ids = _seed(conn, hook_text=hook_text)
    write_clips(conn, vod_id, clip_ids)
    row = conn.execute("SELECT hook FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    assert row["hook"] == "Apres, il y a encore des possibilites"  # "possibilit" evite
    assert len(row["hook"]) <= 40
    assert not row["hook"].endswith((" ", ",", ".", ";", ":"))  # aucune coupe crue


def test_fallback_hook_strips_trailing_punctuation_left_by_the_cut(conn, monkeypatch):
    """Reproduit l'accroche livree en Task 10 (« guillemet ouvrant orphelin en fin
    de fragment) : la ponctuation trainante doit disparaitre, pas seulement le mot
    coupe."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    hook_text = "Mais quoi ? Il vendait trop bien et maintenant, je dis «"
    vod_id, clip_ids = _seed(conn, hook_text=hook_text)
    write_clips(conn, vod_id, clip_ids)
    row = conn.execute("SELECT hook FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    assert row["hook"] == "Mais quoi ? Il vendait trop bien et"
    assert "«" not in row["hook"]
    assert not row["hook"].endswith((",", "«", " "))


def test_fallback_hook_never_keeps_the_blanks_of_the_transcript(conn, monkeypatch):
    """D6 : les mots de la transcription portent deja leur espace de tete, et le
    " ".join du decoupage en ajoute un second. Ces blancs doubles mangeaient le
    budget de 40 caracteres et se voyaient sur la bande."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    hook_text = " Sur  la  route  des  lapins  !  Sur  la  rigolade "
    vod_id, clip_ids = _seed(conn, hook_text=hook_text)
    write_clips(conn, vod_id, clip_ids)
    row = conn.execute("SELECT hook FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    assert "  " not in row["hook"]
    assert row["hook"] == "Sur la route des lapins ! Sur la"
    assert not row["hook"].endswith(" ")


def test_fallback_title_inherits_the_word_safe_truncated_hook(conn, monkeypatch):
    """D3 : « et elle contamine aussi le nom du fichier » -- le titre de repli
    (utilise pour nommer le fichier) doit heriter de l'accroche deja propre, pas
    d'un fragment brut coupe en plein mot."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    hook_text = "Oh, les enfants ! Sur la route des lapins ! Sur la"
    vod_id, clip_ids = _seed(conn, hook_text=hook_text)
    write_clips(conn, vod_id, clip_ids)
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    assert row["title"] == row["hook"] == "Oh, les enfants ! Sur la route des"


def test_ai_success_writes_columns_and_rejects_out_of_set_emoji(conn):
    vod_id, clip_ids = _seed(conn, n=2)
    fake = MagicMock()
    fake.parsed_output = WritingResult(clips=[
        ClipWriting(clip_id=clip_ids[0], hook="GG WP", titre="clip un", emoji="💀"),
        ClipWriting(clip_id=clip_ids[1], hook="INSANE CLUTCH", titre="clip deux", emoji="🚀"),
    ])
    fake.stop_reason = "end_turn"
    with patch("anthropic.Anthropic") as cls:
        cls.return_value.messages.parse.return_value = fake
        ok = write_clips(conn, vod_id, clip_ids)
    assert ok is True
    row1 = conn.execute("SELECT * FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    row2 = conn.execute("SELECT * FROM clips WHERE id=?", (clip_ids[1],)).fetchone()
    assert row1["hook"] == "GG WP" and row1["title"] == "clip un" and row1["emoji"] == "💀"
    assert row2["hook"] == "INSANE CLUTCH"
    assert row2["emoji"] is None  # 🚀 hors du jeu vendorise -> NULL


def test_an_ai_hook_over_the_ceiling_is_cut_on_a_word_not_inside_one(conn):
    """Le plafond defensif de 60 caracteres coupait en plein mot (« INCROYA »),
    la ou le repli local recule deja jusqu'au dernier espace trois lignes plus
    bas. Une accroche s'affiche en grand sur la bande camera : un mot tranche s'y
    voit exactement autant qu'il vienne de l'IA ou du repli.
    """
    vod_id, clip_ids = _seed(conn, n=1)
    long_hook = "IL RETOURNE LA PARTIE TOUT SEUL AVEC UN PIXEL DE VIE INCROYABLE MAIS BON"
    assert long_hook[59] != " " and long_hook[60] != " "  # la coupe crue tombe DANS un mot
    fake = MagicMock()
    fake.parsed_output = WritingResult(clips=[
        ClipWriting(clip_id=clip_ids[0], hook=long_hook, titre="clip un", emoji=None),
    ])
    fake.stop_reason = "end_turn"
    with patch("anthropic.Anthropic") as cls:
        cls.return_value.messages.parse.return_value = fake
        write_clips(conn, vod_id, clip_ids)
    row = conn.execute("SELECT hook FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    assert row["hook"] == "IL RETOURNE LA PARTIE TOUT SEUL AVEC UN PIXEL DE VIE"
    assert len(row["hook"]) <= 60


def test_ai_response_omitting_a_clip_falls_back_for_that_one_only(conn):
    vod_id, clip_ids = _seed(conn, n=2, hook_text="Accroche locale de secours")
    fake = MagicMock()
    fake.parsed_output = WritingResult(clips=[
        ClipWriting(clip_id=clip_ids[0], hook="GG WP", titre="clip un", emoji=None),
    ])
    fake.stop_reason = "end_turn"
    with patch("anthropic.Anthropic") as cls:
        cls.return_value.messages.parse.return_value = fake
        ok = write_clips(conn, vod_id, clip_ids)
    assert ok is False  # repli partiel applique
    row1 = conn.execute("SELECT * FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    row2 = conn.execute("SELECT * FROM clips WHERE id=?", (clip_ids[1],)).fetchone()
    assert row1["hook"] == "GG WP" and row1["title"] == "clip un"  # valeurs IA conservees
    assert row2["hook"] == "Accroche locale de secours"  # repli local pour celui-ci seulement


def test_hallucinated_clip_id_in_response_is_dropped_without_crashing(conn):
    """L'IA peut renvoyer un clip_id qui ne correspond a aucun clip demande (id
    invente, doublon d'un ancien run, etc.) : ce n'est ni une exception ni un repli
    pour le clip reellement demande, qui recoit normalement ses valeurs IA."""
    vod_id, clip_ids = _seed(conn, n=1)
    fake = MagicMock()
    fake.parsed_output = WritingResult(clips=[
        ClipWriting(clip_id=clip_ids[0], hook="GG WP", titre="clip un", emoji=None),
        ClipWriting(clip_id=99999, hook="FANTOME", titre="clip fantome", emoji=None),
    ])
    fake.stop_reason = "end_turn"
    with patch("anthropic.Anthropic") as cls:
        cls.return_value.messages.parse.return_value = fake
        ok = write_clips(conn, vod_id, clip_ids)
    assert ok is True  # rien n'est tombe en repli pour un clip reellement demande
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    assert row["hook"] == "GG WP" and row["title"] == "clip un"
    assert conn.execute("SELECT COUNT(*) FROM clips WHERE vod_id=?",
                        (vod_id,)).fetchone()[0] == 1  # aucune ligne fantome creee


def test_emoji_without_hook_can_never_be_written(conn):
    vod_id, clip_ids = _seed(conn, n=1)
    fake = MagicMock()
    fake.parsed_output = WritingResult(clips=[
        ClipWriting(clip_id=clip_ids[0], hook="", titre="clip un", emoji="💀"),
    ])
    fake.stop_reason = "end_turn"
    with patch("anthropic.Anthropic") as cls:
        cls.return_value.messages.parse.return_value = fake
        write_clips(conn, vod_id, clip_ids)
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    assert row["hook"] is None
    assert row["emoji"] is None  # emoji valide mais accroche vide -> jamais ecrit


def test_api_called_exactly_once_for_several_clips(conn):
    vod_id, clip_ids = _seed(conn, n=3)
    fake = MagicMock()
    fake.parsed_output = WritingResult(clips=[
        ClipWriting(clip_id=cid, hook="HOOK", titre="titre", emoji=None) for cid in clip_ids
    ])
    fake.stop_reason = "end_turn"
    with patch("anthropic.Anthropic") as cls:
        cls.return_value.messages.parse.return_value = fake
        write_clips(conn, vod_id, clip_ids)
        assert cls.return_value.messages.parse.call_count == 1


def test_ai_failure_falls_back_for_every_requested_clip(conn):
    vod_id, clip_ids = _seed(conn, n=2, hook_text="Accroche de secours")
    with patch("anthropic.Anthropic") as cls:
        cls.return_value.messages.parse.side_effect = RuntimeError("api down")
        ok = write_clips(conn, vod_id, clip_ids)
    assert ok is False
    for cid in clip_ids:
        row = conn.execute("SELECT * FROM clips WHERE id=?", (cid,)).fetchone()
        assert row["hook"] == "Accroche de secours"
        assert row["emoji"] is None


def test_max_tokens_stop_reason_falls_back(conn):
    vod_id, clip_ids = _seed(conn, hook_text="Accroche de secours")
    fake = MagicMock()
    fake.parsed_output = None
    fake.stop_reason = "max_tokens"
    with patch("anthropic.Anthropic") as cls:
        cls.return_value.messages.parse.return_value = fake
        ok = write_clips(conn, vod_id, clip_ids)
    assert ok is False
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_ids[0],)).fetchone()
    assert row["hook"] == "Accroche de secours"


def test_empty_clip_ids_is_a_noop(conn):
    vod_id, _ = _seed(conn)
    assert write_clips(conn, vod_id, []) is True


def test_la_troncature_ne_coupe_jamais_au_milieu_d_une_elision():
    """L'accroche est coupee sur le dernier espace : une elision recollee n'en
    porte plus, elle passe donc entiere ou pas du tout. Test de verrouillage du
    couple jonction / troncature."""
    from clipforge.pipeline.ai_writing import _truncate_word_safe

    assert _truncate_word_safe("c'est pas moi", 5) == "c'est"
    assert _truncate_word_safe("il dit c'est", 9) == "il dit"
    assert _truncate_word_safe("allez-vous bien", 12) == "allez-vous"
