"""Camera moves for screen-recording demos (zoompan-based).

This build's `crop` filter evaluates w/h only at init, so it cannot animate a
zoom. `zoompan` evaluates every frame by design, so all moves are built on it.

push(target, D)          gradual zoom in, point of interest pinned (no drift)
pull(target, D)          ease back out to the full frame
hold(target)             static, held at the target magnification
pan(a, b, D)             constant magnification, translate along one axis
spotlight(rect)          dim everything outside rect, no motion
spotlight_push(rect, D)  dim outside rect AND zoom toward it together
"""

OW, OH = 1920, 1080
SS = 2                      # supersample: sub-pixel placement at output res
FPS = 30


def _ease(u, amount=0.55):
    """Blend linear with smoothstep. 0 = constant rate, 1 = full S-curve."""
    ss = f"(3*pow({u},2)-2*pow({u},3))"
    return f"({u}*{1-amount}+{ss}*{amount})"


def _zp(zexpr, xexpr, yexpr, frames):
    """zoompan over a supersampled input, back down to output size."""
    return (f"scale={OW*SS}:{OH*SS}:flags=lanczos,"
            f"zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}'"
            f":d=1:s={OW*SS}x{OH*SS}:fps={FPS},"
            f"scale={OW}:{OH}:flags=lanczos,format=yuv420p")


def _anchor(target):
    """Screen fractions that keep `target` centred on arrival, with no drift."""
    x, y, w, h = target
    return x / (OW - w), y / (OH - h), OW / w


def _motion(target, D, reverse=False):
    x, y, w, h = target
    fx, fy, Z = _anchor(target)
    n = max(int(round(D * FPS)), 1)
    u = f"min(on/{n},1)"
    p = _ease(u)
    if reverse:
        p = f"(1-{p})"
    z = f"pow({Z},{p})"                       # geometric: constant perceived rate
    # zoompan x/y are top-left in input space; this pins the anchor on screen
    xx = f"{fx}*(iw-iw/({z}))"
    yy = f"{fy}*(ih-ih/({z}))"
    return _zp(z, xx, yy, n)


def push(target, D):
    return _motion(target, D, reverse=False)


def pull(target, D):
    return _motion(target, D, reverse=True)


def hold(target):
    fx, fy, Z = _anchor(target)
    return _zp(f"{Z}", f"{fx}*(iw-iw/{Z})", f"{fy}*(ih-ih/{Z})", 1)


def pan(a, b, D):
    """Hold magnification from rect `a`; translate to rect `b`."""
    ax, ay, aw, ah = a
    bx, by = b[0], b[1]
    Z = OW / aw
    n = max(int(round(D * FPS)), 1)
    p = _ease(f"min(on/{n},1)")
    xx = f"({ax}+({bx}-{ax})*{p})*{SS}*(iw/{OW*SS})"
    yy = f"({ay}+({by}-{ay})*{p})*{SS}*(ih/{OH*SS})"
    return _zp(f"{Z}", xx, yy, n)


# ---------- spotlight ----------

def spotlight_mask(rect, path, feather=24, radius=26):
    """Write the alpha mask a spotlight needs."""
    from PIL import Image, ImageDraw, ImageFilter
    x, y, w, h = rect
    m = Image.new("L", (OW, OH), 0)
    ImageDraw.Draw(m).rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=255)
    m.filter(ImageFilter.GaussianBlur(feather)).save(path)
    return path


def spotlight(dim=0.16, sat=0.74):
    """filter_complex body: input [0:v] + mask [1:v] -> [v]. No motion."""
    return (f"[0:v]eq=brightness=-{dim}:saturation={sat}[dark];"
            f"[0:v][1:v]alphamerge[fg];"
            f"[dark][fg]overlay=0:0,fps={FPS},format=yuv420p[v]")


def spotlight_push(target, D, dim=0.16, sat=0.74):
    """Dim outside `target` AND zoom toward it at the same time.

    The mask is composited FIRST, then the whole treated frame is pushed, so the
    dimmed border travels with the picture instead of sliding over it.
    """
    return (f"[0:v]eq=brightness=-{dim}:saturation={sat}[dark];"
            f"[0:v][1:v]alphamerge[fg];"
            f"[dark][fg]overlay=0:0[lit];"
            f"[lit]{push(target, D)}[v]")


# ---------- framing helpers ----------

def focus(point, Z, ow=OW, oh=OH):
    """Rect of magnification Z centred on `point`, clamped inside the frame.

    Use this instead of hand-writing a rect: a hand-written rect silently
    centres the move (fx=0.5) or pushes the anchor outside 0..1, and both
    faults look like "the zoom drifts to the middle".
    """
    cx, cy = point
    w, h = ow / Z, oh / Z
    x = min(max(cx - w / 2, 0), ow - w)
    y = min(max(cy - h / 2, 0), oh - h)
    return (int(round(x)), int(round(y)), int(round(w)), int(round(h)))


def rate_to_Z(rate_per_s, D):
    """Total magnification for a per-second rate held for D seconds."""
    return rate_per_s ** D


CARD_RATE = 1.009      # the tested card-push rate: a drift, not a move
