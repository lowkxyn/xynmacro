"""Generate the app icon: the letter "X" set in Chakra Petch Bold (the same face as
the "XynMacro" wordmark) filled with the brand purple gradient and a soft diagonal
fold for depth — a real letterform, not two crossed bars. Also outputs a white
silhouette of the same glyph for the in-app brand mark.

    cd bot
    py scripts/make_icon.py
    npm run tauri -- icon src-tauri/icons/icon_src.png
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont

S, OUT = 2048, 1024
HERE = os.path.dirname(__file__)
FONT = os.path.join(HERE, "ChakraPetch-Bold.ttf")

# Purple palette, light -> dark.
LIGHT = (193, 168, 252)
MID   = (139, 92, 246)
DEEP  = (88, 40, 194)

def diag_grad(c1, c2):
    """Diagonal gradient, c1 at top-left blending to c2 at bottom-right."""
    t = np.add.outer(np.linspace(0, 1, S), np.linspace(0, 1, S)) / 2.0
    arr = (np.array(c1)[None, None, :] * (1 - t)[:, :, None]
           + np.array(c2)[None, None, :] * t[:, :, None]).astype(np.uint8)
    return Image.fromarray(arr, "RGB")

# --- render the X glyph centred, sized to ~84% of the canvas height ---
glyph = Image.new("L", (S, S), 0)
gd = ImageDraw.Draw(glyph)
size = 1600
font = ImageFont.truetype(FONT, size)
bbox = gd.textbbox((0, 0), "X", font=font)
gh = bbox[3] - bbox[1]
size = int(size * (S * 0.84) / gh)                      # rescale to target height
font = ImageFont.truetype(FONT, size)
bbox = gd.textbbox((0, 0), "X", font=font)
gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]
ox = (S - gw) // 2 - bbox[0]
oy = (S - gh) // 2 - bbox[1]
gd.text((ox, oy), "X", fill=255, font=font)

# --- fill: diagonal gradient, plus a darker fold on the lower-left for a beveled look ---
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
img.paste(diag_grad(LIGHT, DEEP), (0, 0), glyph)

tri = Image.new("L", (S, S), 0)
ImageDraw.Draw(tri).polygon([(0, 0), (S, S), (0, S)], fill=255)   # lower-left of the TL->BR fold
fold = Image.composite(glyph, Image.new("L", (S, S), 0), tri)    # glyph ∩ that triangle
img.paste(diag_grad(MID, DEEP), (0, 0), fold)

icon_path = os.path.abspath(os.path.join(HERE, "..", "src-tauri", "icons", "icon_src.png"))
img.resize((OUT, OUT), Image.LANCZOS).save(icon_path)
print(f"wrote {icon_path}")

# --- in-app brand mark: solid white glyph silhouette (CSS mask uses the alpha only) ---
white = Image.new("RGBA", (S, S), (0, 0, 0, 0))
white.paste((255, 255, 255, 255), (0, 0), glyph)
brand_path = os.path.abspath(os.path.join(HERE, "..", "src", "assets", "content.png"))
white.resize((OUT, OUT), Image.LANCZOS).save(brand_path)
print(f"wrote {brand_path}")
