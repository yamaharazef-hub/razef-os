#!/usr/bin/env python3
"""
Razef OS -- brand asset generator.

Takes the two raw art files in branding/master/ and produces every asset the
image needs:

    assets/logo-1024.png       transparent mark, 1024x1024  (source of truth)
    assets/logo-512.png        transparent mark, 512x512
    assets/logo-256.png        transparent mark, 256x256
    assets/logo-128.png        transparent mark, 128x128
    assets/watermark.png       boot splash mark + glow, 344px wide
    assets/throbber-00NN.png   36 frame spinner, 96x96
    assets/wallpaper.png       2560x1440, dark + faint embossed mark
    assets/wallpaper-4k.png    3840x2160

Run:  python3 branding/build-assets.py
Re-run any time you swap the master art.

Requires: Pillow  (pip install pillow)
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageChops
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required:  pip install pillow")

ROOT = Path(__file__).resolve().parent
MASTER_LOGO = ROOT / "master" / "logo.png"
MASTER_WALL = ROOT / "master" / "wallpaper.png"
OUT = ROOT / "assets"

# Brand palette -------------------------------------------------------------
CYAN = (34, 211, 238)
VIOLET = (124, 58, 237)
BG_DARK = (5, 7, 13)

# Spinner -------------------------------------------------------------------
THROBBER_FRAMES = 36
THROBBER_DOTS = 10
THROBBER_SIZE = 128


def log(msg: str) -> None:
    print(f"  [assets] {msg}")


# ---------------------------------------------------------------------------
# 1. Logo: knock the solid black backdrop out of the generated art and turn it
#    into a clean alpha channel so the mark sits on any background.
# ---------------------------------------------------------------------------
def load_mark() -> Image.Image:
    """Turn the generated art (bright mark on pure black) into a clean RGBA mark.

    The art is effectively "premultiplied against black", so luminance becomes
    the alpha channel and every colour is divided back out by that alpha. That
    keeps the cyan -> violet gradient saturated instead of greying it out.
    """
    import numpy as np

    if not MASTER_LOGO.exists():
        sys.exit(f"missing master logo: {MASTER_LOGO}")

    arr = np.asarray(Image.open(MASTER_LOGO).convert("RGB"), dtype=np.float32) / 255.0
    lum = arr[..., 0] * 0.2126 + arr[..., 1] * 0.7152 + arr[..., 2] * 0.0722

    lo, hi = 0.035, 0.78          # knee points for the alpha ramp
    alpha = np.clip((lum - lo) / (hi - lo), 0.0, 1.0) ** 1.35
    alpha[lum < 0.05] = 0.0       # hard-kill the black backdrop + jpeg noise

    h, w = alpha.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = np.clip((xx / w) * 0.72 + (yy / h) * 0.28, 0.0, 1.0)[..., None]

    cyan = np.array(CYAN, dtype=np.float32) / 255.0
    violet = np.array(VIOLET, dtype=np.float32) / 255.0
    color = cyan * (1.0 - t) + violet * t          # brand gradient fill

    # keep a little of the original inner shading for depth
    shade = np.clip(lum / np.maximum(lum.max(), 1e-3), 0.0, 1.0)[..., None]
    color = np.clip(color * (0.93 + 0.07 * shade), 0.0, 1.0)

    # the artwork's bright outline stays white-hot on top of the gradient
    edge = np.clip((lum - 0.88) / 0.12, 0.0, 1.0)[..., None] ** 2
    color = color * (1.0 - edge) + edge

    rgba = np.dstack([color, alpha])
    out = Image.fromarray((rgba * 255).astype(np.uint8), "RGBA")

    # square up on the mark itself
    bbox = out.getchannel("A").point(lambda v: 255 if v > 12 else 0).getbbox()
    if bbox:
        out = out.crop(bbox)
    side = max(out.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(out, ((side - out.width) // 2, (side - out.height) // 2), out)
    return square


def write_logos(mark: Image.Image) -> None:
    for px in (1024, 512, 256, 128):
        path = OUT / f"logo-{px}.png"
        img = mark.resize((px, px), Image.LANCZOS)
        img.save(path, optimize=True)
        log(f"{path.name}  {px}x{px}")


# ---------------------------------------------------------------------------
# 2. Plymouth boot splash watermark: the mark on transparent, with a bloom
#    baked in, sized so it reads well on a 1080p panel.
# ---------------------------------------------------------------------------
def write_watermark(mark: Image.Image) -> None:
    target = 344
    core = mark.resize((target, target), Image.LANCZOS)

    pad = 90
    canvas = Image.new("RGBA", (target + pad * 2, target + pad * 2), (0, 0, 0, 0))

    # bloom: blurred copy underneath, then the crisp mark on top
    bloom = core.filter(ImageFilter.GaussianBlur(28))
    bloom.putalpha(bloom.getchannel("A").point(lambda v: int(v * 0.55)))
    canvas.alpha_composite(bloom, (pad, pad))

    glow = core.filter(ImageFilter.GaussianBlur(8))
    glow.putalpha(glow.getchannel("A").point(lambda v: int(v * 0.85)))
    canvas.alpha_composite(glow, (pad, pad))

    canvas.alpha_composite(core, (pad, pad))
    path = OUT / "watermark.png"
    canvas.save(path, optimize=True)
    log(f"{path.name}  {canvas.width}x{canvas.height}")


# ---------------------------------------------------------------------------
# 3. Spinner animation frames for the plymouth two-step/throbber module.
# ---------------------------------------------------------------------------
def write_throbber() -> None:
    size = THROBBER_SIZE
    radius = 40.0
    dot_r = 6.2

    for frame in range(THROBBER_FRAMES):
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img, "RGBA")
        head = frame / THROBBER_FRAMES * math.tau

        for i in range(THROBBER_DOTS):
            # dots trail behind the head around the circle
            a = head - i * 0.16
            fade = 1.0 - i / THROBBER_DOTS
            mix = i / max(1, THROBBER_DOTS - 1)
            col = tuple(
                int(CYAN[c] * (1 - mix) + VIOLET[c] * mix) for c in range(3)
            ) + (int(255 * fade ** 1.3),)
            cx = size / 2 + math.cos(a) * radius
            cy = size / 2 + math.sin(a) * radius
            r = dot_r * (0.62 + 0.38 * fade)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)

        img.save(OUT / f"throbber-{frame + 1:04d}.png", optimize=True)
    log(f"throbber-0001.png .. throbber-{THROBBER_FRAMES:04d}.png ({THROBBER_SIZE}px)")


# ---------------------------------------------------------------------------
# 4. Wallpaper: dark grade + vignette + very faint embossed mark, bottom right.
# ---------------------------------------------------------------------------
def write_wallpaper(mark: Image.Image) -> None:
    if not MASTER_WALL.exists():
        sys.exit(f"missing master wallpaper: {MASTER_WALL}")

    for name, size in (("wallpaper.png", (2560, 1440)), ("wallpaper-4k.png", (3840, 2160))):
        w, h = size
        base = Image.open(MASTER_WALL).convert("RGB").resize(size, Image.LANCZOS)
        base = ImageEnhance.Brightness(base).enhance(0.82)
        base = ImageEnhance.Color(base).enhance(1.08)

        # vignette
        vig = Image.new("L", (w, h), 0)
        dv = ImageDraw.Draw(vig)
        dv.ellipse([-w * 0.35, -h * 0.35, w * 1.35, h * 1.35], fill=255)
        vig = vig.filter(ImageFilter.GaussianBlur(w // 8))
        dark = Image.new("RGB", (w, h), (0, 0, 0))
        base = Image.composite(base, dark, vig)

        # faint embossed mark, lower right
        m = mark.resize((int(h * 0.62), int(h * 0.62)), Image.LANCZOS)
        ghost = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ghost.paste(m, (w - m.width + int(m.width * 0.12), h - m.height + int(m.height * 0.10)), m)
        ghost.putalpha(ghost.getchannel("A").point(lambda v: int(v * 0.085)))
        canvas = Image.alpha_composite(base.convert("RGBA"), ghost).convert("RGB")

        path = OUT / name
        canvas.save(path, optimize=True, quality=95)
        log(f"{path.name}  {w}x{h}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    log("knocking out logo background ...")
    mark = load_mark()
    write_logos(mark)
    write_watermark(mark)
    write_throbber()
    write_wallpaper(mark)
    log("done")


if __name__ == "__main__":
    main()
