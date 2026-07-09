"""Genereert icon.png / icon@2x.png / logo.png in de stijl van icon.svg:
batterij met bliksem, electric-blue -> teal gradient, transparante achtergrond.
Voor de home-assistant/brands PR en de README."""
from PIL import Image, ImageDraw

C1 = (41, 120, 214)   # electric blue
C2 = (27, 175, 122)   # teal


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make(size):
    s = size / 256.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # batterijbehuizing (afgerond, verticale gradient via clip-mask)
    bx0, by0, bx1, by1 = 56 * s, 40 * s, 200 * s, 232 * s
    body = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(body)
    for y in range(int(20 * s), int(by1)):
        t = max((y - by0) / (by1 - by0), 0.0)
        bd.line([(bx0, y), (bx1, y)], fill=lerp(C1, C2, t) + (255,))
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([bx0, by0, bx1, by1], radius=28 * s, fill=255)
    # batterijpool
    md.rounded_rectangle([104 * s, 20 * s, 152 * s, 44 * s], radius=8 * s, fill=255)
    img.paste(body, (0, 0), mask)

    # bliksem (wit)
    bolt = [(150 * s, 68 * s), (96 * s, 148 * s), (126 * s, 148 * s),
            (106 * s, 204 * s), (164 * s, 118 * s), (132 * s, 118 * s)]
    d.polygon(bolt, fill=(255, 255, 255, 245))
    return img


make(256).save("icon.png")
make(512).save("icon@2x.png")
make(512).save("logo.png")
print("icons geschreven")
