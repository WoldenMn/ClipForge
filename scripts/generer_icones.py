# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.40", "pillow>=10"]
# ///
"""Regenere toutes les icones de ClipForge depuis les deux SVG de assets/.

assets/icon.svg sert a partir de 48 px, assets/icon-petit.svg en dessous : a
16-32 px le cadre de VOD, l'etincelle et les degrades deviennent du bruit, la
variante petite est dessinee sur la grille de 32 px pour rester nette.

Lancement : uv run scripts/generer_icones.py

Le build Tauri ne surveille pas src-tauri/icons : sans cargo clean -p clipforge
--release, l'exe recompile garde l'ancienne icone.
"""

import io
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

RACINE = Path(__file__).resolve().parent.parent
SOURCE_GRANDE = RACINE / "assets" / "icon.svg"
SOURCE_PETITE = RACINE / "assets" / "icon-petit.svg"
ICONES = RACINE / "src-tauri" / "icons"
FAVICON = RACINE / "frontend" / "public" / "icon.svg"
SEUIL_PETIT = 44

PNG_TAURI = {
    "32x32.png": 32,
    "64x64.png": 64,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
    "Square30x30Logo.png": 30,
    "Square44x44Logo.png": 44,
    "Square71x71Logo.png": 71,
    "Square89x89Logo.png": 89,
    "Square107x107Logo.png": 107,
    "Square142x142Logo.png": 142,
    "Square150x150Logo.png": 150,
    "Square284x284Logo.png": 284,
    "Square310x310Logo.png": 310,
    "StoreLogo.png": 50,
}
TAILLES_ICO = [16, 24, 32, 48, 64, 128, 256]


def source_pour(taille: int) -> str:
    chemin = SOURCE_PETITE if taille <= SEUIL_PETIT else SOURCE_GRANDE
    return chemin.read_text(encoding="utf-8")


def rendre(page, taille: int) -> Image.Image:
    svg = source_pour(taille)
    page.set_viewport_size({"width": taille, "height": taille})
    page.set_content(
        "<!doctype html><html><head><style>"
        "html,body{margin:0;padding:0;background:transparent}"
        f"svg{{display:block;width:{taille}px;height:{taille}px}}"
        f"</style></head><body>{svg}</body></html>"
    )
    png = page.screenshot(
        omit_background=True, clip={"x": 0, "y": 0, "width": taille, "height": taille}
    )
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    if image.size != (taille, taille):
        raise RuntimeError(f"rendu {taille} px : taille obtenue {image.size}")
    return image


def main() -> int:
    for source in (SOURCE_GRANDE, SOURCE_PETITE):
        if not source.is_file():
            print(f"source absente : {source}", file=sys.stderr)
            return 1

    tailles = sorted(set(PNG_TAURI.values()) | set(TAILLES_ICO))
    with sync_playwright() as pw:
        navigateur = pw.chromium.launch()
        try:
            page = navigateur.new_page(device_scale_factor=1)
            rendus = {t: rendre(page, t) for t in tailles}
        finally:
            navigateur.close()

    for nom, taille in PNG_TAURI.items():
        rendus[taille].save(ICONES / nom, format="PNG", optimize=True)

    images_ico = [rendus[t] for t in sorted(TAILLES_ICO, reverse=True)]
    images_ico[0].save(
        ICONES / "icon.ico",
        format="ICO",
        sizes=[(t, t) for t in TAILLES_ICO],
        append_images=images_ico[1:],
    )

    FAVICON.write_text(SOURCE_GRANDE.read_text(encoding="utf-8"), encoding="utf-8")

    print(
        f"{len(PNG_TAURI)} PNG + icon.ico ({', '.join(map(str, TAILLES_ICO))}) dans {ICONES}"
    )
    print(f"favicon : {FAVICON}")
    print(
        "ensuite, dans src-tauri : cargo clean -p clipforge --release, puis cargo tauri build"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
