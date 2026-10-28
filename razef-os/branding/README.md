# Branding pipeline

```
master/logo.png  ──┐
master/wallpaper.png ─┴─▶ build-assets.py ──▶ assets/*.png ──▶ build.sh ──▶ image
```

`master/` is the source of truth: replace those two files and run `just brand`.

## What the logo pipeline does to `master/logo.png`

1. **Luminance → alpha.** The art is a glowing mark on pure black, which is
   effectively premultiplied alpha. Brightness becomes the alpha channel and
   everything below 5% is forced transparent.
2. **Re-colour from the brand gradient.** The original pixels are only used for
   the shape and its inner shading; the fill is a diagonal `CYAN → VIOLET`
   gradient so the mark stays saturated at 24 px and at 1024 px.
3. **White-hot edge.** The brightest outline pixels stay white, which is what
   makes the mark readable on a dark boot screen.
4. **Trim + square**, then emit 1024/512/256/128 px.

## Outputs

| File | Consumer |
|---|---|
| `logo-*.png` | `/usr/share/pixmaps/razef-os.png`, hicolor icons, GDM logo |
| `watermark.png` | plymouth `razef` theme |
| `throbber-00NN.png` | plymouth spinner (36 frames, 128 px) |
| `box.png`, `lock.png` | plymouth password dialog |
| `wallpaper.png`, `wallpaper-4k.png` | GNOME background |

`boot-preview.png` and `asset-preview.png` are just contact sheets for review;
they are not installed.

## Requirements

`python3 -m pip install pillow` (numpy is optional but the un-premultiply path is
pure numpy — install it: `pip install pillow numpy`).
