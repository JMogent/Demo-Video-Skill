"""Detect the region of interest in a screen recording by where pixels CHANGE.

On a screen capture the eye follows what is changing - text appearing, a panel
populating, a response streaming. That is measurable without any knowledge of
the application, so it works on any layout.
"""
import subprocess, os, glob, shutil
import numpy as np
from PIL import Image

GRID = 48          # analysis block size at 480x270 proxy
PROXY = (480, 270)


def sample(src, start, end, tag, fps=4):
    d = f"working/fx/roi_{tag}"
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-to", str(end),
                    "-i", src, "-vf", f"fps={fps},scale={PROXY[0]}:{PROXY[1]}",
                    f"{d}/%04d.png"], check=True)
    return sorted(glob.glob(f"{d}/*.png"))


def change_map(frames):
    """Accumulated absolute change per pixel across the window."""
    acc = None
    prev = None
    for f in frames:
        a = np.asarray(Image.open(f).convert("L"), dtype=float)
        if prev is not None:
            d = np.abs(a - prev)
            acc = d if acc is None else acc + d
        prev = a
    return acc


def roi(acc, keep=0.55, blocks=(24, 14), pad_blocks=1):
    """Bounding box of the blocks holding `keep` of all change.

    Block aggregation is what makes this work: compression noise is spread
    thinly over the whole frame, real activity is concentrated. Summing into
    coarse blocks first lets a strict threshold ignore the noise floor.
    """
    h, w = acc.shape
    bx, by = blocks
    bh, bw = h // by, w // bx
    grid = acc[:by * bh, :bx * bw].reshape(by, bh, bx, bw).sum(axis=(1, 3))
    # subtract the noise floor: the median block is background
    grid = np.maximum(grid - np.median(grid), 0)
    total = grid.sum()
    if total <= 0:
        return None, 0.0
    order = np.argsort(grid.ravel())[::-1]
    cum = np.cumsum(grid.ravel()[order])
    n = int(np.searchsorted(cum, total * keep)) + 1
    sel = np.zeros(grid.size, bool)
    sel[order[:n]] = True
    sel = sel.reshape(by, bx)
    ys, xs = np.where(sel)
    x0 = max(xs.min() - pad_blocks, 0) / bx
    x1 = min(xs.max() + 1 + pad_blocks, bx) / bx
    y0 = max(ys.min() - pad_blocks, 0) / by
    y1 = min(ys.max() + 1 + pad_blocks, by) / by
    return (x0, y0, x1 - x0, y1 - y0), round((x1 - x0) * (y1 - y0), 3)


def to_169(box, W=1920, H=1080, min_w=640):
    """Snap a normalised box to a 16:9 rect in pixel space."""
    x, y, w, h = box
    px, py, pw, ph = x * W, y * H, w * W, h * H
    cx, cy = px + pw / 2, py + ph / 2
    need_w = max(pw, ph * 16 / 9, min_w)
    need_h = need_w * 9 / 16
    if need_w > W:
        need_w, need_h = W, H
    nx = min(max(cx - need_w / 2, 0), W - need_w)
    ny = min(max(cy - need_h / 2, 0), H - need_h)
    return (round(nx), round(ny), round(need_w), round(need_h))


def analyse(src, start, end, tag):
    frames = sample(src, start, end, tag)
    acc = change_map(frames)
    box, conc = roi(acc)
    if box is None:
        return None
    rect = to_169(box)
    mag = 1920 / rect[2]
    return {"rect": rect, "coverage": round(conc, 3), "magnification": round(mag, 2),
            "frames": len(frames)}
