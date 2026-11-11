import pytest
from pydantic import ValidationError

from clipforge.models import SubtitleStyle
from clipforge.render.ass import _sanitize, hex_to_ass, sec_to_ass


def test_hex_to_ass_bgr_order():
    assert hex_to_ass("#FF8000") == "&H000080FF"  # RGB FF,80,00 -> BGR 00,80,FF


def test_sec_to_ass():
    assert sec_to_ass(3661.25) == "1:01:01.25"
    assert sec_to_ass(0.0) == "0:00:00.00"


def test_sanitize_neutralizes_ass_markup_and_newlines():
    """Les accolades ouvriraient une balise ASS, l'antislash une commande."""
    assert _sanitize("{rires}") == "(rires)"
    assert _sanitize("avant\r\napres") == "avant apres"
    assert _sanitize("\\pos(0,0)") == "pos(0,0)"


def test_subtitle_style_rejects_invalid_hex():
    with pytest.raises(ValidationError):
        SubtitleStyle(primary_hex="FFF")
