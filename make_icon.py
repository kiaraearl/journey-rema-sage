"""
Generates dashboard.ico — pixel art sword icon for Job Hunt HQ.
16×16 logical grid, 3px per cell = 48×48 pixels.
"""
from PIL import Image
from pathlib import Path

OUT = Path(__file__).parent / "dashboard.ico"

S = 3   # pixels per logical cell

BG    = (4,   4,   12,  255)   # #04040c
BLADE = (255, 45,  120, 255)   # #ff2d78  pink
BSHD  = (160, 20,  70,  255)   # blade right-side shadow
GUARD = (200, 35,  100, 255)   # crossguard
GSHD  = (130, 20,  60,  255)   # guard shadow side
GREEN = (57,  255, 20,  255)   # #39ff14  neon tip
GOLD  = (255, 215, 0,   255)   # #ffd700  pommel
GDSLD = (200, 160, 0,   255)   # pommel underside

img = Image.new("RGBA", (16 * S, 16 * S), BG)
px  = img.load()

def cell(lx, ly, c):
    for dy in range(S):
        for dx in range(S):
            px[lx * S + dx, ly * S + dy] = c

# ── Blade (cols 7-8, rows 0-11) ───────────────────────────────────────────
for row in range(1, 12):
    cell(7, row, BLADE)
    cell(8, row, BSHD)

# ── Green neon tip (row 0) ────────────────────────────────────────────────
cell(7, 0, GREEN)
cell(8, 0, GREEN)

# ── Crossguard (rows 5-6, cols 3-12) ─────────────────────────────────────
for col in range(3, 13):
    for row in (5, 6):
        if col == 7:
            cell(col, row, BLADE)          # blade passes through guard
        elif col == 8:
            cell(col, row, BSHD)
        elif col in (3, 12):
            cell(col, row, GSHD)           # guard tips slightly darker
        else:
            cell(col, row, GUARD if row == 5 else GSHD)

# ── Pommel (rows 12-13, cols 6-9) ────────────────────────────────────────
for col in range(6, 10):
    cell(col, 12, GOLD)
    cell(col, 13, GDSLD)

# ── Multi-size ICO (16, 32, 48) ───────────────────────────────────────────
img16 = img.resize((16, 16), Image.Resampling.NEAREST)
img32 = img.resize((32, 32), Image.Resampling.NEAREST)
img48 = img   # already 48×48

img48.save(
    OUT,
    format="ICO",
    sizes=[(16, 16), (32, 32), (48, 48)],
)

print("Icon saved:", OUT)
