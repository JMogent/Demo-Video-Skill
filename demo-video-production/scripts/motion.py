"""Motion-graphics kit for the demo video skill.

Builds title cards, number cross-outs, typed scripts, word-by-word captions, audio
waveforms, timeline assembly, clocks, process lines, credit rolls and version
triptychs as video, using only Pillow, numpy and ffmpeg. No browser engine needed.

Tested 20-23 Nov 2026 on a 2:28 music-led film (11 verse clips, two interludes,
outro). Each piece renders at 1080p in roughly real time to 3x real time.

Usage
  python motion.py demo OUT.mp4        # 12s sampler: proves the environment works
  python motion.py webm IN.webm OUT.mp4  # Clipchamp .webm -> constant-30fps .mp4
  from motion import *                 # build your own frame(t) and call render()

Pattern
  Every effect is a function draw_xxx(layer, t_local, ...) that draws onto a
  transparent RGBA layer, where t_local is seconds since the effect started.
  A film is a frame(t) function that picks which effects are live at time t,
  composites them over background(t) and returns an RGB image. render() streams
  frames to ffmpeg. render_chunks() renders in parts and joins them, which
  survives a lost session: a finished part is never redone.
"""
import math, os, random, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1920, 1080, 30

# ---------------------------------------------------------------- theme
def _font_path(family, style):
    try:
        out = subprocess.run(['fc-match', '-f', '%{file}', f'{family}:style={style}'],
                             capture_output=True, text=True).stdout.strip()
        if out: return out
    except Exception: pass
    return None

class Theme:
    """One token block per film. Swap the values, keep every effect."""
    def __init__(self, bg=(11, 61, 46), bg_edge=(6, 36, 27), text=(252, 246, 232),
                 accent=(232, 184, 74), strike=(200, 16, 46),
                 serif='Gelasio', sans='Carlito', particles='snow'):
        self.bg, self.bg_edge, self.text, self.accent, self.strike = bg, bg_edge, text, accent, strike
        self.serif, self.sans, self.particles = serif, sans, particles
        self._cache = {}
    def font(self, size, serif=False, style='Bold'):
        key = (int(size), serif, style)
        if key not in self._cache:
            p = _font_path(self.serif if serif else self.sans, style) or _font_path('DejaVu Sans', 'Bold')
            self._cache[key] = ImageFont.truetype(p, max(2, int(size)))
        return self._cache[key]

FESTIVE = Theme()
NEUTRAL = Theme(bg=(28, 32, 44), bg_edge=(14, 16, 24), accent=(110, 205, 177), particles=None)

# ---------------------------------------------------------------- easing and layers
def clamp(t): return max(0.0, min(1.0, t))
def ease_out(t): return 1 - (1 - clamp(t)) ** 3
def ease_back(t, s=1.9):
    t = clamp(t) - 1
    return t * t * ((s + 1) * t + s) + 1
def new_layer(): return Image.new('RGBA', (W, H), (0, 0, 0, 0))
def fade(layer, a):
    a = clamp(a)
    layer.putalpha(layer.split()[3].point(lambda v: int(v * a)))
    return layer
def window(t, start, end, fade_in=0.3, fade_out=0.3):
    """Opacity for an element live from start to end, 0 outside it."""
    if t < start or t >= end: return 0.0
    return min(clamp((t - start) / fade_in) if fade_in else 1, clamp((end - t) / fade_out) if fade_out else 1)

# ---------------------------------------------------------------- background
_BG = {}
def background(t, theme=FESTIVE, seed=4, count=160):
    key = id(theme)
    if key not in _BG:
        yy, xx = np.mgrid[0:H, 0:W]
        d = np.sqrt(((xx - W / 2) / W) ** 2 + ((yy - H / 2) / H) ** 2)
        k = np.clip(d * 1.6, 0, 1)[..., None]
        img = Image.fromarray((np.array(theme.bg) * (1 - k) + np.array(theme.bg_edge) * k).astype(np.uint8), 'RGB')
        rnd = random.Random(seed)
        parts = [(rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(1.5, 4.5), rnd.uniform(25, 70), rnd.uniform(0, 6.28)) for _ in range(count)]
        _BG[key] = (img, parts)
    img, parts = _BG[key]
    im = img.copy()
    if theme.particles == 'snow':
        d = ImageDraw.Draw(im, 'RGBA')
        for x, y, r, v, ph in parts:
            y2 = (y + v * t) % H; x2 = x + 12 * math.sin(t * 0.8 + ph)
            d.ellipse([x2 - r, y2 - r, x2 + r, y2 + r], fill=(255, 255, 255, int(90 + 30 * r)))
    return im

# ---------------------------------------------------------------- effects
def draw_heading(layer, text, theme=FESTIVE, y=200, size=54):
    d = ImageDraw.Draw(layer)
    d.text((W / 2, y), text, font=theme.font(size, True, 'Italic'), fill=theme.text, anchor='mm')
    d.line([(W / 2 - 120, y + 50), (W / 2 + 120, y + 50)], fill=theme.accent, width=4)

def draw_title_card(layer, t, kicker, title, subtitle, chips=(), theme=FESTIVE):
    """Kicker, title, rule, subtitle, then chips appearing one by one."""
    d = ImageDraw.Draw(layer)
    for start, y, txt, f, col in [(0.3, 250, kicker, theme.font(40), theme.accent),
                                   (0.8, 400, title, theme.font(140, True), theme.text),
                                   (2.0, 560, subtitle, theme.font(54, False, 'Regular'), theme.text)]:
        a = ease_out((t - start) / 0.6)
        if a > 0:
            d.text((W / 2, y + (1 - a) * 30), txt, font=f, fill=col + (int(255 * a),), anchor='mm')
    a = ease_out((t - 1.4) / 0.5)
    if a > 0: d.line([(W / 2 - 160 * a, 500), (W / 2 + 160 * a, 500)], fill=theme.accent, width=5)
    f = theme.font(40); widths = [d.textlength(c, font=f) + 70 for c in chips]; gap = 24
    x = (W - (sum(widths) + gap * (len(chips) - 1))) / 2
    for i, (c, w) in enumerate(zip(chips, widths)):
        a = ease_out((t - 3.0 - i * 0.35) / 0.4)
        if a > 0:
            yo = (1 - a) * 25
            d.rounded_rectangle([x, 700 + yo, x + w, 770 + yo], radius=35, fill=theme.accent + (int(40 * a),), outline=theme.accent + (int(255 * a),), width=3)
            d.text((x + w / 2, 735 + yo), c, font=f, fill=theme.text + (int(255 * a),), anchor='mm')
        x += w + gap

def draw_strike_replace(layer, t, old, new, label, theme=FESTIVE, cx=W / 2, baseline=640,
                        scale=1.0, t_strike=1.6, t_pop=2.0, slide_in=True):
    """'12 hours recording' -> red stroke through 12 -> gold '1' pops in above it."""
    d = ImageDraw.Draw(layer)
    nf = theme.font(230 * scale, True); tf = theme.font(110 * scale)
    nw = d.textlength(old, font=nf); lw = d.textlength(label, font=tf); gap = 50 * scale
    x0 = cx - (nw + gap + lw) / 2
    yb = baseline + ((1 - ease_out(t / 0.45)) * 60 if slide_in else 0)
    d.text((x0, yb), old, font=nf, fill=theme.text if t < t_strike else theme.text + (140,), anchor='ls')
    d.text((x0 + nw + gap, yb - 20 * scale), label, font=tf, fill=theme.text, anchor='ls')
    s = ease_out((t - t_strike) / 0.35)
    if s > 0:
        xa, ya, xb, yb2 = x0 - 25 * scale, yb - 60 * scale, x0 + nw + 25 * scale, yb - 125 * scale
        d.line([(xa, ya), (xa + (xb - xa) * s, ya + (yb2 - ya) * s)], fill=theme.strike, width=int(22 * scale))
    q = clamp((t - t_pop) / 0.4)
    if q > 0:
        size = (150 if len(new) > 2 else 170) * scale * max(0.01, ease_back(q))
        d.text((x0 + nw / 2, yb - 235 * scale), new, font=theme.font(size, True), fill=theme.accent, anchor='mm')

def draw_subtitle(layer, t, text, theme=FESTIVE, y=925, size=56):
    """The gold line in brackets under a clip."""
    r = ease_out(t / 0.4)
    ImageDraw.Draw(layer).text((W / 2, y + (1 - r) * 15), text, font=theme.font(size, True, 'Italic'),
                               fill=theme.accent + (int(255 * r),), anchor='mm')

def draw_typewriter(layer, t, text, box, theme=FESTIVE, size=44, duration=2.6):
    x, y, w = box
    d = ImageDraw.Draw(layer); f = theme.font(size, True, 'Italic')
    shown = text[:int(len(text) * clamp(t / duration))]
    lines, cur = [], ''
    for word in shown.split(' '):
        if d.textlength((cur + ' ' + word).strip(), font=f) > w: lines.append(cur); cur = word
        else: cur = (cur + ' ' + word).strip()
    lines.append(cur)
    for j, l in enumerate(lines):
        caret = '|' if j == len(lines) - 1 and int(t * 3) % 2 else ''
        d.text((x, y + j * size * 1.36), l + caret, font=f, fill=theme.text, anchor='lm')

def draw_word_captions(layer, t, text, theme=FESTIVE, y=H - 110, size=56, per_word=None):
    """Caption chip; words light up in sequence, current word in the accent colour."""
    d = ImageDraw.Draw(layer); f = theme.font(size); words = text.split()
    per_word = per_word or 3.4 / len(words)
    k = min(len(words), int(t / per_word) + 1)
    tw = d.textlength(' '.join(words), font=f); x = (W - tw) / 2
    d.rounded_rectangle([x - 30, y - size * 0.85, x + tw + 30, y + size * 0.85], radius=16, fill=(0, 0, 0, 185))
    for j, wd in enumerate(words):
        col = theme.accent if j == k - 1 else (theme.text if j < k else theme.text + (60,))
        d.text((x, y), wd, font=f, fill=col, anchor='lm'); x += d.textlength(wd + ' ', font=f)

_ENV = {}
def audio_envelope(path, hop_s=0.01):
    if path not in _ENV:
        raw = subprocess.run(['ffmpeg', '-loglevel', 'error', '-i', path, '-ac', '1', '-ar', '8000', '-f', 's16le', '-'], capture_output=True).stdout
        a = np.frombuffer(raw, np.int16).astype(float) / 32768; hop = int(8000 * hop_s); n = len(a) // hop
        env = np.sqrt((a[:n * hop].reshape(n, hop) ** 2).mean(1)); _ENV[path] = env / (env.max() or 1)
    return _ENV[path]

def draw_waveform(layer, t, audio_path, box, theme=FESTIVE, bars=170, duration=4.0):
    """Scrolling bar waveform from a real audio file; bars fill with the accent as it plays."""
    x0, yc, w, hmax = box
    env = audio_envelope(audio_path); n = len(env); d = ImageDraw.Draw(layer)
    off = int(t * 100)
    for b in range(bars):
        v = env[(off + b * 4) % n] * (0.25 + 0.75 * clamp(t / 0.5)); x = x0 + b * w / bars; h = 8 + v * hmax
        col = theme.accent if b < bars * clamp(t / duration) else theme.accent + (110,)
        d.rounded_rectangle([x, yc - h / 2, x + w / bars * 0.6, yc + h / 2], radius=2, fill=col)

def draw_clock(layer, t, centre, radius, start_hours, end_hours, run_time, theme=FESTIVE):
    cx, cy = centre; R = radius; d = ImageDraw.Draw(layer)
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=theme.accent, width=6)
    for h in range(12):
        a = h / 12 * 2 * math.pi
        d.line([(cx + math.sin(a) * (R - 22), cy - math.cos(a) * (R - 22)), (cx + math.sin(a) * (R - 6), cy - math.cos(a) * (R - 6))], fill=theme.text, width=4)
    hrs = start_hours + clamp(t / run_time) * (end_hours - start_hours)
    for frac, length, width, col in [((hrs % 12) / 12, 0.5, 10, theme.text), (hrs % 1, 0.8, 6, theme.accent)]:
        a = frac * 2 * math.pi
        d.line([(cx, cy), (cx + math.sin(a) * R * length, cy - math.cos(a) * R * length)], fill=col, width=width)
    d.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], fill=theme.accent)

def draw_timestamps(layer, t, events, origin, theme=FESTIVE, step=82, every=0.75):
    """events = [("10:07", "Request handed over"), ...] revealed one by one."""
    x, y0 = origin; d = ImageDraw.Draw(layer)
    for j, (tm, lab) in enumerate(events):
        q = ease_out((t - 0.2 - j * every) / 0.4)
        if q > 0:
            y = y0 + j * step + (1 - q) * 20
            d.text((x, y), tm, font=theme.font(40, True), fill=theme.accent + (int(255 * q),), anchor='lm')
            d.text((x + 130, y), lab, font=theme.font(32, False, 'Regular'), fill=theme.text + (int(255 * q),), anchor='lm')

def draw_process(layer, t, steps, theme=FESTIVE, y=500, every=0.3, note=None):
    """Audit > Brief > Cut ... pills appearing left to right, optional note underneath."""
    d = ImageDraw.Draw(layer); f = theme.font(44); gap = 40
    bw = max(d.textlength(s, font=f) for s in steps) + 60
    x = (W - (bw * len(steps) + gap * (len(steps) - 1))) / 2
    for j, s in enumerate(steps):
        q = ease_out((t - 0.2 - j * every) / 0.3)
        if q > 0:
            yo = (1 - q) * 30
            d.rounded_rectangle([x, y + yo, x + bw, y + 100 + yo], radius=50, fill=theme.accent + (int(60 * q),), outline=theme.accent, width=3)
            d.text((x + bw / 2, y + 50 + yo), s, font=f, fill=theme.text, anchor='mm')
            if j < len(steps) - 1: d.text((x + bw + gap / 2, y + 50), '›', font=theme.font(50), fill=theme.accent, anchor='mm')
        x += bw + gap
    if note:
        q = clamp((t - 0.2 - len(steps) * every - 0.3) / 0.3)
        if q > 0: d.text((W / 2, y + 230), note, font=theme.font(50, True, 'Italic'), fill=theme.accent + (int(255 * q),), anchor='mm')

def build_roll(sections, theme=FESTIVE, closer=None):
    """sections = [(heading, [bullet, ...]), ...] -> a tall transparent image for draw_roll."""
    rows = []
    for h, items in sections: rows += [('h', h)] + [('b', b) for b in items] + [('s', '')]
    if closer: rows += [('s', ''), ('c', closer)]
    hgt = {'h': 90, 'c': 90, 'b': 58, 's': 40}
    roll = Image.new('RGBA', (W, sum(hgt[k] for k, _ in rows) + 40), (0, 0, 0, 0)); d = ImageDraw.Draw(roll); y = 0
    for k, tx in rows:
        if k == 'h': d.text((W / 2, y + 45), tx, font=theme.font(58, True), fill=theme.accent, anchor='mm')
        elif k == 'c': d.text((W / 2, y + 45), tx, font=theme.font(58, True, 'BoldItalic'), fill=theme.accent, anchor='mm')
        elif k == 'b': d.text((W / 2, y + 29), tx, font=theme.font(40, False, 'Regular'), fill=theme.text, anchor='mm')
        y += hgt[k]
    return roll

def draw_roll(layer, t, roll, top=300, bottom=1000, land_at=25.0, land_y=620):
    """Credit roll that scrolls up and LANDS its last line at land_y, then holds."""
    speed = (bottom - land_y + roll.height - 85) / land_at
    y = bottom - speed * min(t, land_at)
    src_top = max(0, int(top - y)); dst_top = int(max(top, y)); h = bottom - dst_top
    if h > 0 and src_top < roll.height:
        layer.alpha_composite(roll.crop((0, src_top, W, min(roll.height, src_top + h))), (0, dst_top))
    a = np.array(layer)
    for yy in range(top, top + 80): a[yy, :, 3] = (a[yy, :, 3] * ((yy - top) / 80)).astype(np.uint8)
    for yy in range(bottom - 80, bottom): a[yy, :, 3] = (a[yy, :, 3] * ((bottom - yy) / 80)).astype(np.uint8)
    layer.paste(Image.fromarray(a), (0, 0))

def draw_sparkles(layer, t, centre=(W / 2, 580), rx=700, ry=220, count=14, strength=1.0, theme=FESTIVE):
    d = ImageDraw.Draw(layer)
    for j in range(count):
        ang = j / count * 6.283 + t * 0.6; r = 1 + 0.06 * math.sin(t * 3 + j)
        sx = centre[0] + rx * r * math.cos(ang); sy = centre[1] + ry * r * math.sin(ang); sz = (6 + 4 * math.sin(t * 5 + j)) * strength
        d.polygon([(sx, sy - sz * 2), (sx + sz * .5, sy), (sx, sy + sz * 2), (sx - sz * .5, sy)], fill=theme.text)
        d.polygon([(sx - sz * 2, sy), (sx, sy - sz * .5), (sx + sz * 2, sy), (sx, sy + sz * .5)], fill=theme.text)

def draw_cross(layer, t, box, theme=FESTIVE, at=0.0, label=None):
    """Big red X through a region (e.g. an old tool's timeline), then an optional verdict chip."""
    x0, y0, x1, y1 = box; d = ImageDraw.Draw(layer)
    for k, (a, b) in enumerate([((x0, y0), (x1, y1)), ((x1, y0), (x0, y1))]):
        s = ease_out((t - at - k * 0.35) / 0.35)
        if s > 0: d.line([a, (a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s)], fill=theme.strike, width=26)
    q = clamp((t - at - 0.9) / 0.4)
    if label and q > 0:
        f = theme.font(48); tw = d.textlength(label, font=f); cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        d.rounded_rectangle([cx - tw / 2 - 30, cy - 45, cx + tw / 2 + 30, cy + 45], radius=18, fill=theme.bg_edge + (int(235 * q),), outline=theme.accent + (int(255 * q),), width=3)
        d.text((cx, cy), label, font=f, fill=theme.text + (int(255 * q),), anchor='mm')

# ---------------------------------------------------------------- footage helpers
class FrameReader:
    """Sequential frames from a video, resized to size. Call .at(t_local)."""
    def __init__(self, path, start=0.0, speed=1.0, size=(W, H)):
        self.size = size; w, h = size
        self.p = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-ss', str(start), '-i', path, '-an', '-vf',
                                   f'setpts=PTS/{speed},fps={FPS},scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2',
                                   '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.i, self.f = -1, None
    def at(self, t):
        want = int(t * FPS); n = self.size[0] * self.size[1] * 3
        while self.i < want:
            b = self.p.stdout.read(n)
            if len(b) < n: break
            self.f = Image.frombytes('RGB', self.size, b); self.i += 1
        return self.f

def still(path, t, size=(W, H)):
    b = subprocess.run(['ffmpeg', '-loglevel', 'error', '-ss', str(t), '-i', path, '-frames:v', '1', '-vf', f'scale={size[0]}:{size[1]}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
    return Image.frombytes('RGB', size, b)

def push(img, t, target_box, duration=1.6, delay=0.2, out_size=None):
    """Ease a crop from the full frame to target_box. Apply to a STILL or a static shot,
    never to footage that already carries its own move (moves compound)."""
    s = ease_out((t - delay) / duration); W0, H0 = img.size
    box = [a + (b - a) * s for a, b in zip((0, 0, W0, H0), target_box)]
    return img.crop(tuple(int(v) for v in box)).resize(out_size or img.size, Image.LANCZOS), box

def spotlight(img, rect, strength=1.0, dim=160):
    m = Image.new('L', img.size, int(dim * clamp(strength))); ImageDraw.Draw(m).rounded_rectangle(rect, radius=24, fill=0)
    m = m.filter(ImageFilter.GaussianBlur(10))
    return Image.composite(Image.new('RGB', img.size, (0, 0, 0)), img.convert('RGB'), m)

def webm_to_mp4(src, dst):
    """Clipchamp records .webm with no duration in the header and a variable frame rate.
    Re-encode to constant 30fps before probing, seeking or cutting."""
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', src, '-r', str(FPS), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', dst], check=True)
    return dst

# ---------------------------------------------------------------- rendering
def render(frame_fn, start, end, out, size=(W, H)):
    p = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{size[0]}x{size[1]}',
                          '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', out], stdin=subprocess.PIPE)
    for f in range(int(round(start * FPS)), int(round(end * FPS))):
        p.stdin.write(frame_fn(f / FPS).tobytes())
    p.stdin.close(); p.wait()
    return out

def render_chunks(frame_fn, cuts, out, audio=None, workdir='.'):
    """cuts = [0, 36, 69, 105, 148.3]. Each part is skipped if it already exists, so a run
    interrupted by a lost session resumes where it stopped. Cut at scene boundaries so any
    FrameReader starts at the head of its clip."""
    parts = []
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        p = os.path.join(workdir, f'part_{i:02d}.mp4')
        if not os.path.exists(p) or os.path.getsize(p) < 1000:
            tmp = p + '.tmp.mp4'; render(frame_fn, a, b, tmp); os.replace(tmp, p)
        parts.append(p)
    lst = os.path.join(workdir, 'parts.txt')
    with open(lst, 'w') as fh: fh.writelines(f"file '{os.path.abspath(p)}'\n" for p in parts)
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', lst]
    if audio: cmd += ['-i', audio, '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest']
    else: cmd += ['-c', 'copy']
    subprocess.run(cmd + [out], check=True)
    return out

# ---------------------------------------------------------------- self-test
def _demo_frame(t, theme=FESTIVE):
    im = background(t, theme); L = new_layer()
    if t < 4:
        draw_title_card(L, t, 'MOTION KIT', 'It works here', 'Pillow + numpy + ffmpeg, no browser', ['Cards', 'Cross-out', 'Captions'], theme)
        fade(L, window(t, 0, 4))
    elif t < 8:
        draw_heading(L, 'On the twelfth day of Christmas, Cowork gave to me…', theme)
        draw_strike_replace(L, t - 4, '12', '1', 'hours recording', theme)
        if t > 6.3: draw_subtitle(L, t - 6.3, '(works with raw, rough footage)', theme)
        fade(L, window(t, 4, 8))
    else:
        draw_process(L, t - 8, ['Audit', 'Brief', 'Cut', 'Voice', 'Deliver'], theme, note='Never render before the Brief is agreed.')
        draw_word_captions(L, t - 8, 'Captions light up word by word', theme)
        fade(L, window(t, 8, 12))
    im.paste(L, (0, 0), L)
    return im

if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'demo':
        render(_demo_frame, 0, 12, sys.argv[2]); print('ok', sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == 'webm':
        print('ok', webm_to_mp4(sys.argv[2], sys.argv[3]))
    else:
        print(__doc__)
