#!/usr/bin/env python3
"""Draw the app icon and compile it into CTS MOA Engine.app.

Generated rather than hand-drawn, like the explainer deck, so it can be
re-rendered after a tweak. Needs Pillow (present on this machine) plus the
macOS `iconutil`; if either is missing the app simply keeps the default
AppleScript icon, which is cosmetic only.

The design has to survive being 32 pixels wide in a Finder list, so it is
deliberately one bold shape: a hexagonal ring of atoms -- the universal
shorthand for "molecule" -- with a single vertex picked out in amber. That
highlighted atom is the whole point of the tool: finding the one new agent
among many.

Run:  python3 build_icon.py
"""
import math
import os
import shutil
import subprocess
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:                                          # pragma: no cover
    print("Pillow is not installed — keeping the default icon.")
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "CTS MOA Engine.app")
S = 1024                       # master size; every other size is downsampled

INK_TOP = (26, 95, 180)        # --blue from the GUI, so the app and page match
INK_BOTTOM = (12, 48, 105)
RING = (255, 255, 255)
ACCENT = (255, 176, 46)


def _rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1],
                                        radius=radius, fill=255)
    return m


def _gradient(size, top, bottom):
    g = Image.new("RGB", (1, size))
    px = g.load()
    for y in range(size):
        t = y / (size - 1)
        px[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
    return g.resize((size, size))


def draw(size=S):
    # 4x supersampling: PIL has no antialiased line drawing, and at icon sizes
    # the difference between jagged and smooth bonds is the whole impression.
    ss = 4
    n = size * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    img.paste(_gradient(n, INK_TOP, INK_BOTTOM), (0, 0))
    img.putalpha(_rounded_mask(n, int(n * 0.225)))           # macOS squircle-ish

    d = ImageDraw.Draw(img)
    cx = cy = n / 2
    r = n * 0.232                      # ring radius, leaving room for the branch
    node = n * 0.066                   # atom radius
    accent_node = n * 0.078            # the highlighted one, slightly larger
    bond = int(n * 0.038)              # bond thickness

    # Point-right hexagon: flat top and bottom read as more stable at 32px.
    pts = [(cx + r * math.cos(math.radians(60 * i)),
            cy + r * math.sin(math.radians(60 * i))) for i in range(6)]
    HILITE = 5                         # upper-right vertex

    for i in range(6):
        d.line([pts[i], pts[(i + 1) % 6]], fill=RING, width=bond)

    # A branch off the highlighted atom, so it reads as "singled out" rather
    # than merely mis-coloured. Drawn before the atoms so the bond tucks under
    # them; the offsets are measured from the vertex outward, not from the
    # canvas centre, which is what made an earlier version overlap into a blob.
    hx, hy = pts[HILITE]
    ux, uy = (hx - cx) / r, (hy - cy) / r
    branch = n * 0.150                 # vertex -> satellite centre
    sat = n * 0.050
    sx, sy = hx + ux * branch, hy + uy * branch
    d.line([(hx, hy), (sx, sy)], fill=ACCENT, width=bond)

    for i, (x, y) in enumerate(pts):
        fill = ACCENT if i == HILITE else RING
        rad = accent_node if i == HILITE else node
        d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=fill)
    d.ellipse([sx - sat, sy - sat, sx + sat, sy + sat], fill=ACCENT)

    return img.resize((size, size), Image.LANCZOS)


def main():
    master = draw()
    preview = os.path.join(HERE, "icon-preview.png")
    master.save(preview)
    print(f"drew {preview}")

    if not shutil.which("iconutil"):
        print("iconutil not found (macOS only) — stopping after the preview.")
        return 0

    iconset = os.path.join(HERE, "icon.iconset")
    os.makedirs(iconset, exist_ok=True)
    for px in (16, 32, 128, 256, 512):
        master.resize((px, px), Image.LANCZOS).save(
            os.path.join(iconset, f"icon_{px}x{px}.png"))
        master.resize((px * 2, px * 2), Image.LANCZOS).save(
            os.path.join(iconset, f"icon_{px}x{px}@2x.png"))

    icns = os.path.join(HERE, "app.icns")
    p = subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns],
                       capture_output=True, text=True)
    shutil.rmtree(iconset, ignore_errors=True)
    if p.returncode != 0:
        print("iconutil failed:", (p.stderr or "").strip()[:300])
        return 1

    dest = os.path.join(APP, "Contents", "Resources", "applet.icns")
    if not os.path.isdir(os.path.dirname(dest)):
        print(f"{APP} not found — built {icns}, but did not install it.")
        return 0
    shutil.copyfile(icns, dest)
    os.remove(icns)

    # Finder caches icons aggressively; bumping the bundle's mtime and
    # re-signing is what actually makes the new one show up.
    subprocess.run(["touch", APP], capture_output=True)
    subprocess.run(["codesign", "--force", "--deep", "-s", "-", APP],
                   capture_output=True)
    print(f"installed into {os.path.basename(APP)}")
    print("If Finder still shows the old icon, it is cached — the app itself "
          "is updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
