from pydantic import BaseModel, Field


class SubtitleStyle(BaseModel):
    font_name: str = "Segoe UI"
    font_size: int = 88
    primary_hex: str = Field("#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")      # mot courant
    # mots a venir (SecondaryColour)
    highlight_hex: str = Field("#FFD700", pattern=r"^#[0-9A-Fa-f]{6}$")
    outline_hex: str = Field("#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    outline_px: int = 4
    margin_v: int = 220
    uppercase: bool = True
    max_words_per_line: int = 4
