"""Genereer consistente Wattson-brandassets voor HACS, HA en de README."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
BRAND = ROOT / "custom_components" / "wattson_ems" / "brand"
C_BLUE = (47, 127, 226)
C_TEAL = (19, 184, 135)
C_TEXT = (18, 36, 58)
C_SUB = (57, 112, 134)


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def font(size: int, bold: bool = False):
    candidates = [
        Path("C:/Windows/Fonts") / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def make_icon(size: int) -> Image.Image:
    scale = size / 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient = Image.new("RGBA", image.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(gradient)
    for y in range(size):
        gd.line((0, y, size, y), fill=lerp(C_BLUE, C_TEAL, y / max(size - 1, 1)) + (255,))

    mask = Image.new("L", image.size, 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((48 * scale, 40 * scale, 208 * scale, 232 * scale), radius=36 * scale, fill=255)
    md.rounded_rectangle((104 * scale, 20 * scale, 152 * scale, 48 * scale), radius=9 * scale, fill=255)
    image.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(image)
    bolt = [(151, 66), (92, 151), (126, 151), (106, 208), (168, 117), (133, 117)]
    draw.polygon([(x * scale, y * scale) for x, y in bolt], fill=(255, 255, 255, 255))
    # Een subtiele energiegolf maakt het merk eigen zonder op klein formaat te storen.
    points = [(72, 187), (89, 175), (106, 175), (124, 187), (141, 199), (159, 199), (184, 187)]
    draw.line([(x * scale, y * scale) for x, y in points], fill=(255, 255, 255, 56), width=max(2, round(8 * scale)), joint="curve")
    return image


def make_logo(width: int, dark: bool = False) -> Image.Image:
    height = width // 3
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    icon_size = round(height * 0.86)
    icon = make_icon(icon_size)
    image.alpha_composite(icon, (round(height * 0.08), (height - icon_size) // 2))
    draw = ImageDraw.Draw(image)
    title_color = (242, 248, 252) if dark else C_TEXT
    sub_color = (139, 215, 222) if dark else C_SUB
    x = round(height * 1.12)
    draw.text((x, round(height * 0.24)), "WATTSON", font=font(round(height * 0.29), True), fill=title_color + (255,))
    draw.text((x + 3, round(height * 0.59)), "SLIMME THUISACCU", font=font(round(height * 0.105), True), fill=sub_color + (255,))
    return image


def save_assets() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    icon = make_icon(256)
    icon_2x = make_icon(512)
    logo = make_logo(768)
    logo_2x = make_logo(1536)
    dark_logo = make_logo(768, dark=True)
    dark_logo_2x = make_logo(1536, dark=True)

    for path, asset in (
        (ROOT / "icon.png", icon),
        (ROOT / "icon@2x.png", icon_2x),
        (ROOT / "logo.png", logo),
        (ROOT / "logo@2x.png", logo_2x),
        (BRAND / "icon.png", icon),
        (BRAND / "icon@2x.png", icon_2x),
        (BRAND / "dark_icon.png", icon),
        (BRAND / "dark_icon@2x.png", icon_2x),
        (BRAND / "logo.png", logo),
        (BRAND / "logo@2x.png", logo_2x),
        (BRAND / "dark_logo.png", dark_logo),
        (BRAND / "dark_logo@2x.png", dark_logo_2x),
    ):
        asset.save(path, optimize=True)

    print(f"Wattson-assets geschreven naar {ROOT} en {BRAND}")


if __name__ == "__main__":
    save_assets()
