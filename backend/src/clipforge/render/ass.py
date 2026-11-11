"""Primitives ASS partagees : couleurs, temps, echappement du texte utilisateur."""


def hex_to_ass(hex_color: str) -> str:
    r, g, b = hex_color.lstrip("#")[0:2], hex_color.lstrip("#")[2:4], hex_color.lstrip("#")[4:6]
    return f"&H00{b}{g}{r}".upper()


def sec_to_ass(t: float) -> str:
    cs = round(max(0.0, t) * 100)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _sanitize(text: str) -> str:
    return (text.replace("\\", "").replace("{", "(").replace("}", ")")
                .replace("\r", "").replace("\n", " "))
