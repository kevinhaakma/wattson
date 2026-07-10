"""Genereer consistente Wattson-brandassets voor HACS, HA en de README.

Het master-icoon wordt procedureel getekend volgens de brand-sheet:
accu met afgeronde hoeken, verticaal verloop blauw -> teal -> groen,
witte bliksem en een golf onderin. Palet:
  #0D1B2A (navy)  #1565C0 (blauw)  #00B4B0 (teal)  #22C55E (groen)  #F2F4F7 (licht)
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
BRAND = ROOT / "custom_components" / "wattson_ems" / "brand"
MASTER_ICON = ROOT / "assets" / "wattson-icon-master.png"

# ---- brand-palet ----------------------------------------------------------
NAVY = (13, 27, 42)        # #0D1B2A — tekst / donker
BLUE = (21, 101, 192)      # #1565C0 — verloop top
TEAL = (0, 180, 176)       # #00B4B0 — verloop midden + subtitel
GREEN = (34, 197, 94)      # #22C55E — verloop onder
LIGHT = (242, 244, 247)    # #F2F4F7 — tekst op donker

SS = 2  # supersampling-factor voor gladde randen


def font(size: int, bold: bool = False):
    candidates = [
        Path("C:/Windows/Fonts") / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _lerp(c1, c2, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def _vgradient(width: int, height: int, stops) -> Image.Image:
    """Verticale gradient; stops = [(pos 0..1, rgb), ...] gesorteerd."""
    img = Image.new("RGBA", (width, height))
    px = img.load()
    for y in range(height):
        t = y / max(height - 1, 1)
        for (p1, c1), (p2, c2) in zip(stops, stops[1:]):
            if t <= p2 or (p2 == stops[-1][0]):
                local = 0.0 if p2 == p1 else min(max((t - p1) / (p2 - p1), 0.0), 1.0)
                color = _lerp(c1, c2, local)
                break
        row = tuple(color) + (255,)
        for x in range(width):
            px[x, y] = row
    return img


def _bolt(draw: ImageDraw.ImageDraw, box, fill):
    """Klassieke bliksemschicht in de opgegeven (x0, y0, x1, y1)-box."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    pts = [(0.585, 0.00), (0.10, 0.62), (0.395, 0.62),
           (0.32, 1.00), (0.90, 0.36), (0.52, 0.36)]
    draw.polygon([(x0 + px * w, y0 + py * h) for px, py in pts], fill=fill)


def make_master() -> Image.Image:
    """Teken het brand-icoon (transparant, 1024x1024) volgens de sheet."""
    S = 1024 * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # accu-geometrie
    bx0, bx1 = round(S * 0.265), round(S * 0.735)
    by0, by1 = round(S * 0.185), round(S * 0.920)
    radius = round(S * 0.095)

    # dop
    cap_w, cap_h = round(S * 0.165), round(S * 0.085)
    cap = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(cap).rounded_rectangle(
        [(S - cap_w) // 2, by0 - round(cap_h * 0.72), (S + cap_w) // 2, by0 + radius],
        radius=round(cap_h * 0.3), fill=BLUE + (255,))
    img.alpha_composite(cap)

    # body: gradientmasker
    grad = _vgradient(bx1 - bx0, by1 - by0, [(0.0, BLUE), (0.55, TEAL), (1.0, GREEN)])
    mask = Image.new("L", (bx1 - bx0, by1 - by0), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bx1 - bx0 - 1, by1 - by0 - 1], radius=radius, fill=255)
    img.paste(grad, (bx0, by0), mask)

    # donkere rand
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=radius,
                        outline=NAVY + (70,), width=round(S * 0.007))

    # golf onderin (twee lagen), geclipt op de body
    wave = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wave)
    bw = bx1 - bx0
    for y_base, amp, color in (
        (by0 + (by1 - by0) * 0.745, S * 0.022, (255, 255, 255, 45)),
        (by0 + (by1 - by0) * 0.785, S * 0.026, (232, 255, 249, 105)),
    ):
        pts = [(bx0 + i, y_base + amp * math.sin((i / bw) * 2.2 * math.pi + 0.6))
               for i in range(bw + 1)]
        wd.polygon(pts + [(bx1, by1), (bx0, by1)], fill=color)
    clip = Image.new("L", (S, S), 0)
    ImageDraw.Draw(clip).rounded_rectangle([bx0, by0, bx1, by1], radius=radius, fill=255)
    img.paste(wave, (0, 0), Image.composite(wave.getchannel("A"), Image.new("L", (S, S), 0), clip))

    # glans bovenin
    gloss = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gh = round((by1 - by0) * 0.42)
    ggrad = _vgradient(bx1 - bx0, gh, [(0.0, (255, 255, 255)), (1.0, (255, 255, 255))])
    galpha = Image.new("L", (bx1 - bx0, gh))
    gpx = galpha.load()
    for y in range(gh):
        a = round(46 * (1 - y / gh))
        for x in range(bx1 - bx0):
            gpx[x, y] = a
    ggrad.putalpha(galpha)
    gmask = Image.new("L", (bx1 - bx0, gh), 0)
    ImageDraw.Draw(gmask).rounded_rectangle([0, 0, bx1 - bx0 - 1, gh + radius], radius=radius, fill=255)
    gloss.paste(ggrad, (bx0, by0), gmask)
    img.alpha_composite(gloss)

    # bliksem
    bolt_w = round(S * 0.240)
    bolt_h = round(S * 0.360)
    bcx, bcy = (bx0 + bx1) // 2, by0 + round((by1 - by0) * 0.375)
    _bolt(d, (bcx - bolt_w // 2, bcy - bolt_h // 2, bcx + bolt_w // 2, bcy + bolt_h // 2),
          (255, 255, 255, 255))

    return img.resize((1024, 1024), Image.Resampling.LANCZOS)


def make_icon(size: int) -> Image.Image:
    """Maak een vierkant icon uit de brand-master (alpha-bbox + marge)."""
    source = Image.open(MASTER_ICON).convert("RGBA")
    alpha = source.getchannel("A").point(lambda value: 255 if value >= 8 else 0)
    bbox = alpha.getbbox()
    if bbox is None:
        raise RuntimeError(f"Brand-master bevat geen zichtbare pixels: {MASTER_ICON}")

    left, top, right, bottom = bbox
    width, height = right - left, bottom - top
    pad = round(max(width, height) * 0.08)
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(source.width, right + pad)
    bottom = min(source.height, bottom + pad)
    cropped = source.crop((left, top, right, bottom))

    side = max(cropped.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(
        cropped,
        ((side - cropped.width) // 2, (side - cropped.height) // 2),
    )
    return square.resize((size, size), Image.Resampling.LANCZOS)


def _tracked_text(draw, xy, text, fnt, fill, tracking):
    """Tekst met letterspatiëring (PIL kent geen tracking)."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + tracking
    return x - tracking


def make_logo(width: int, dark: bool = False) -> Image.Image:
    """Wordmark volgens de sheet: WATTSON met bliksem in de tweede O,
    daaronder een teal streep + SMART HOME BATTERY."""
    height = width // 3
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    icon_size = round(height * 0.86)
    icon = make_icon(icon_size)
    image.alpha_composite(icon, (round(height * 0.08), (height - icon_size) // 2))
    draw = ImageDraw.Draw(image)

    title_color = LIGHT if dark else NAVY
    disc_color = LIGHT if dark else NAVY
    bolt_color = NAVY if dark else (255, 255, 255)

    title_font = font(round(height * 0.30), True)
    x = round(height * 1.12)
    y_title = round(height * 0.22)

    # WATTS  [O met bolt]  N
    x_after = x
    for ch in "WATTS":
        draw.text((x_after, y_title), ch, font=title_font, fill=title_color + (255,))
        x_after += draw.textlength(ch, font=title_font)

    o_w = draw.textlength("O", font=title_font)
    bbox = draw.textbbox((0, 0), "O", font=title_font)
    o_top, o_bottom = y_title + bbox[1], y_title + bbox[3]
    o_h = o_bottom - o_top
    disc_d = max(o_w, o_h)
    disc_x0 = x_after + (o_w - disc_d) / 2
    disc_y0 = o_top + (o_h - disc_d) / 2
    draw.ellipse([disc_x0, disc_y0, disc_x0 + disc_d, disc_y0 + disc_d], fill=disc_color + (255,))
    inset = disc_d * 0.24
    _bolt(draw, (disc_x0 + inset, disc_y0 + inset * 0.82,
                 disc_x0 + disc_d - inset, disc_y0 + disc_d - inset * 0.82),
          bolt_color + (255,))
    x_after += o_w
    draw.text((x_after, y_title), "N", font=title_font, fill=title_color + (255,))
    x_after += draw.textlength("N", font=title_font)

    # subtitel: streep + SMART HOME BATTERY (teal, getrackt) — automatisch
    # passend gemaakt binnen de canvasbreedte
    sub_text = "SMART HOME BATTERY"
    margin = round(height * 0.10)
    size = round(height * 0.105)
    while size > 8:
        sub_font = font(size, True)
        tracking = round(size * 0.26)
        line_w = round(height * 0.30)
        text_w = sum(draw.textlength(ch, font=sub_font) + tracking for ch in sub_text) - tracking
        if x + 2 + line_w + round(height * 0.07) + text_w <= width - margin:
            break
        size -= 1
    y_sub = round(height * 0.615)
    sub_bbox = draw.textbbox((0, 0), "S", font=sub_font)
    line_y = y_sub + (sub_bbox[1] + sub_bbox[3]) / 2
    draw.line([(x + 2, line_y), (x + 2 + line_w, line_y)], fill=TEAL + (255,), width=max(2, round(height * 0.014)))
    _tracked_text(draw, (x + 2 + line_w + round(height * 0.07), y_sub),
                  sub_text, sub_font, TEAL + (255,), tracking=tracking)

    return image


def save_assets() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    MASTER_ICON.parent.mkdir(parents=True, exist_ok=True)
    make_master().save(MASTER_ICON, optimize=True)

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
        (ROOT / "www" / "wattson-icon.png", icon),
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
