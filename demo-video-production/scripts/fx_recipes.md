# FFmpeg recipes

Tested commands for masking, motion, highlight and overlays. All assume 1920x1080 at 30fps —
adjust coordinates to the source resolution, which you must measure rather than assume.

---

## 1. Surgical masking

Blur only the sensitive token, never the container. One `crop → gblur → overlay` chain per
region, composed into a single `filter_complex`.

```python
def blur_filter(boxes, speed=1.0):
    """boxes: list of (x1, y1, x2, y2, sigma)"""
    parts, last = ["[0:v]copy[base]"], "base"
    for i, (x1, y1, x2, y2, sg) in enumerate(boxes):
        parts.append(f"[0:v]crop={x2-x1}:{y2-y1}:{x1}:{y1},gblur=sigma={sg}[b{i}]")
        parts.append(f"[{last}][b{i}]overlay={x1}:{y1}[ov{i}]")
        last = f"ov{i}"
    parts.append(f"[{last}]setpts=PTS/{speed},fps=30[v]")
    return ["-filter_complex", ";".join(parts), "-map", "[v]"]
```

**Sigma guide:** 4-5 softens text to just-illegible while the shape still reads as text; 7-9 for
a short token inside a line; 12+ looks like a redaction box and draws the eye.

**Text reflows.** In an input box that grows as it is typed, a masked line moves up when a new
line wraps. Find the reflow moment and split the segment there, with a box per position:

```python
import numpy as np
from PIL import Image
a = np.asarray(Image.open(frame).convert('L'))
rows = [y for y, v in enumerate((a[930:1040, 1150:1640] < 120).sum(axis=1)) if v > 20]
```

Compare the top text row across frames; where it jumps, cut the segment.

---

## 2. Camera moves

**Render these LAST, against the recorded narration - not during the cut.** The cue for a move
is the moment the narrator names the region, which is only measurable once the audio exists
(`silencedetect`, s10). Baking a move during the cut means guessing that moment, and the guess
is almost always too early: the eye is pulled to a panel before the line that explains it, and
the rest of the shot is spent watching whatever happens to be ticking there. Keep a flat master
per segment and render the moved version from it, so re-aiming costs one segment.

**Ease a spotlight in; never switch it.** A hard grading change inside one continuous shot reads
as a glitch. Ramp over ~0.7s with `blend` and a time expression:

```
[0:v]eq=brightness=-0.16:saturation=0.74[dk];[0:v][1:v]alphamerge[fg];
[dk][fg]overlay=0:0[lit];
[0:v][lit]blend=all_expr='A*(1-min(max(T/0.7,0),1))+B*min(max(T/0.7,0),1)'[out]
```

Chain `[out]` into `push(...)` for a spotlight that eases in while the camera travels.

**Use `scripts/moves.py`.** It encodes geometry that is easy to get wrong by hand, and it is
built on `zoompan` rather than `crop` for the reason below.

```python
import sys; sys.path.insert(0, 'scripts')
from moves import push, pull, pan, hold, spotlight, spotlight_push, spotlight_mask

RECT = (380, 100, 1040, 585)          # x, y, w, h - keep 16:9 or the scale-back distorts

subprocess.run(["ffmpeg","-y","-ss",str(a),"-to",str(b),"-i",SRC,"-an",
                "-vf", push(RECT, 6.0),
                "-c:v","libx264","-preset","medium","-crf","19",
                "-pix_fmt","yuv420p", dst], check=True)
```

### Why not `crop`

Many ffmpeg builds evaluate `crop`'s **width and height once at initialisation** while
re-evaluating **x and y every frame**. An animated crop therefore snaps to full magnification
on frame one and then slides - which reads as a jump cut followed by a bad pan, not a zoom.
The `eval=frame` option that would fix it does not exist in those builds (`Option not found`).
`zoompan` evaluates per frame by design, so every move here uses it.

### Always test a new move on a still image

On a still, anything that changes on screen is the move:

```bash
ffmpeg -y -loop 1 -framerate 30 -t 7 -i frame.png -an -vf "<move>" \
  -c:v libx264 -crf 19 -pix_fmt yuv420p still_test.mp4
```

Sample the first frame and compare it to the source. Near-identical means the move starts at
full frame; already-magnified means the filter is not animating. This is the check that
catches the `crop` fault in seconds.

### The moves

| Function | What it does |
|---|---|
| `push(rect, D)` | Zoom from full frame toward `rect` over `D` seconds. The point of interest is pinned at a fixed screen position, so there is no sideways drift |
| `pull(rect, D)` | Ease back out to the full frame |
| `pan(a, b, D)` | Hold magnification, translate from rect `a` to rect `b` |
| `hold(rect)` | Static at `rect`'s magnification |
| `spotlight()` | Dim outside the mask, no motion |
| `spotlight_push(rect, D)` | Dim outside the mask **and** zoom toward it together |

Two properties make a push read as one continuous move rather than a zoom-then-pan:

1. **Geometric magnification.** Interpolating width linearly makes the perceived zoom rate
   accelerate, because magnification is multiplicative. `moves.py` interpolates
   `z = Z^p` instead, which holds the rate constant.
2. **A pinned anchor.** The crop window's screen fraction is fixed at `x/(W-w)`, so the point
   of interest never moves. Interpolating x, y, w and h independently drifts the centre - that
   is what produces the "sudden switch, then pan across" feel.

Measured on the reference footage: motion spread across the move 1.54x, frame-to-frame jitter
0.172 (static-footage noise floor is 0.040).

Supersampling to 2x costs roughly 4x render time on those segments. Fine for three or four
moves in a film; do not apply it liberally.

---

## 3. Spotlight

```python
from moves import spotlight_mask, spotlight
spotlight_mask((1385, 35, 355, 505), 'mask.png')      # feathered alpha mask
```

```bash
ffmpeg -y -i in.mp4 -i mask.png -filter_complex "<spotlight()>" -map "[v]" \
  -c:v libx264 -crf 19 -pix_fmt yuv420p out.mp4
```

`dim=0.16` is noticeable without looking broken; 0.25 is heavy. Mask feather ~24px gives an
edge that does not read as a rectangle.

For `spotlight_push`, the mask is composited **first** and the treated frame is then pushed, so
the dimmed border travels with the picture instead of sliding over it. Note that as the zoom
fills the frame there is progressively less surrounding context left to dim, so most of the
spotlight's value lands in the first half of the move.

---


## 4. Persona badge (lower third)

Build a transparent PNG and overlay it for a fixed window, fading in and out.

**Two rules that were learned the hard way, both invisible until rendered:**

**Size the plate from MEASURED text, never a constant.** A fixed plate width clips the longest
line - it truncated "Contoso Bank" to "Contoso B" and the fault only showed up in the film:

```python
tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
tw = max(tmp.textbbox((0, 0), NAME, font=f_name)[2],
         tmp.textbbox((0, 0), ROLE, font=f_role)[2])
PW = 34 + D + 30 + tw + 40          # pad + face + gutter + text + right pad
```

**Crop the face at ~60% of image height starting ~12% down.** A tighter crop (50% from 5% down)
puts the chin outside the circle. Check the rounded result by eye and re-crop: eyes about a third
down, a little air above the head, chin clear of the edge.

```python
s = int(SH * 0.60); x = (SW - s) // 2; y = int(SH * 0.12)
face = src.crop((x, y, x + s, y + s)).resize((D, D), Image.LANCZOS).convert("RGBA")
m = Image.new("L", (D * 4, D * 4), 0)
ImageDraw.Draw(m).ellipse([0, 0, D * 4 - 1, D * 4 - 1], fill=255)
face.putalpha(m.resize((D, D), Image.LANCZOS))      # 4x mask = clean antialiased edge
```

Verify on a frame from the FINISHED film, at a timecode in the middle of the badge's hold - a
grab during the fade shows a ghost and proves nothing.

```python
from PIL import Image, ImageDraw, ImageFont
W, H = 1920, 1080
card = Image.new('RGBA', (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(card)
d.rounded_rectangle([80, 880, 700, 1000], radius=12, fill=(10, 14, 22, 205))
d.rectangle([80, 880, 86, 1000], fill=(0, 160, 220, 255))          # accent edge
name = ImageFont.truetype(FDIR + "NotoSans-Bold.ttf", 34)
role = ImageFont.truetype(FDIR + "NotoSans-Regular.ttf", 24)
d.text((200, 906), "Alex Baker", font=name, fill=(255, 255, 255, 255))
d.text((200, 950), "Corporate Relationship Manager", font=role, fill=(220, 226, 235, 255))
card.save('badge.png')                      # paste a circular headshot at ~(110,895) if supplied
```

```bash
ffmpeg -y -i in.mp4 -loop 1 -framerate 30 -t DURATION -i badge.png -filter_complex \
"[1:v]format=rgba,fade=t=in:st=0.4:d=0.6:alpha=1,fade=t=out:st=4.4:d=0.6:alpha=1[b];\
 [0:v][b]overlay=0:0:enable='between(t,0,5)',fps=30[v]" \
-map "[v]" -c:v libx264 -crf 20 -pix_fmt yuv420p out.mp4
```

Show it for ~5 seconds when the persona first appears, and again after any application switch
that could lose the viewer.

---

## 5. Title and value-summary cards

Same overlay mechanism over a still. On a bright photo, white text needs a scrim — a horizontal
alpha gradient, solid on one side and fading out before the subject.

```python
scrim = Image.new('L', (W, 1), 0)
px = scrim.load()
solid_to, fade_to = int(W * 0.16), int(W * 0.60)
for x in range(W):
    px[x, 0] = 165 if x < solid_to else (
        int(165 * (1 - (x - solid_to) / (fade_to - solid_to))) if x < fade_to else 0)
overlay.paste(Image.new('RGBA', (W, H), (10, 14, 22, 255)), (0, 0), scrim.resize((W, H)))
```

A hard-edged panel reads as a graphic pasted on; a gradient over ~44% of the width reads as
lighting. Structure a value card as: title, accent rule, three short outcome lines, accent rule,
one bold closing line.

**The image must loop.** A single `-i card.png` applies to one frame only. Use
`-loop 1 -framerate 30 -t DURATION` and `overlay=...:shortest=1`.

---

## 6. Fades and joins

```bash
# fade out at the end of a segment of length D
-vf "setpts=PTS/SPEED,fps=30,fade=t=out:st=$(echo "$D-0.5"|bc):d=0.5"
```

Cut the fade **on the application transition**, not near it. Find the exact frame where the new
application appears, end the outgoing segment ~0.1s before, start the incoming segment at it.
A fade placed a second early leaves the old screen visible after the transition.

```bash
# lay narration over finished picture
ffmpeg -y -i picture.mp4 -i narration.m4a -map 0:v:0 -map 1:a:0 \
  -c:v copy -c:a aac -b:a 192k -shortest FINAL.mp4
```

---

## 7. Verification

Never describe a render you have not looked at.

```bash
for t in 10 45 90 150 200; do
  ffmpeg -loglevel error -ss $t -i FINAL.mp4 -frames:v 1 -vf scale=800:-1 -q:v 4 chk_$t.jpg
done
```

View them. Check: masks are on the right tokens, no mask covers story content, transitions land
cleanly, and the on-screen action matches the script at that timecode.

---

## 8. Customer branding

Derive the look from what the user supplied rather than inventing it.

**From a website.** Fetch it and read the CSS custom properties and heading styles; take the
accent, the dark neutral and the heading typeface family. Substitute a bundled font of the same
class when the exact face is unavailable - do not fall back to a default sans without saying so.

**From a logo.** Scale one dimension and let the other follow, so it can never stretch:

```python
logo = Image.open(src).convert("RGBA")
w = 420                                     # target width in a 1920-wide frame
logo = logo.resize((w, round(logo.height * w / logo.width)), Image.LANCZOS)
card.paste(logo, (x, y), logo)              # keep >= half the logo height as clear space
```

A logo on a dark scrim needs its light variant; on a light card, its dark one. If only one
variant exists, put it on a neutral plate rather than recolouring it.

**Palette slots** used by the overlay recipes above - set these once per film:

| Slot | Default | Use |
|---|---|---|
| accent | `#00A0DC` | rules, badge edge |
| scrim | `#0A0E16` at 165-214 alpha | behind text |
| text | `#FFFFFF` / `#CED6E2` | primary / secondary |

**Never sample a brand colour from a compressed screenshot.** JPEG artefacts shift hues; take
the value from the site's CSS, the logo's vector source, or the brand guidelines.

---

## 9. Finding the region of interest

`scripts/roi.py` locates the active region by accumulating frame-to-frame change, aggregating it
into coarse blocks, subtracting the noise floor, and taking the bounding box of the blocks that
hold a majority of the activity. Block aggregation is the part that matters: compression noise is
spread thinly over the whole frame while real activity is concentrated, so summing into blocks
first lets a strict threshold ignore the noise. A per-pixel threshold returns the full frame every
time - that was the first attempt and it failed on every scene.

```python
from roi import analyse
r = analyse("cut.mp4", 66, 82, "deck")
# {'rect': (731, 77, 1097, 617), 'magnification': 1.75, 'coverage': 0.286, 'frames': 64}
```

`rect` is snapped to 16:9 in 1920x1080 space, ready to pass to `push`, `spotlight_push` or
`spotlight_mask`. Tuning knobs: `keep` (share of change to enclose, default 0.55), `blocks`
(grid, default 24x14), `pad_blocks` (margin).

**Always draw the box on a frame and look at it** before rendering a move to it:

```python
from PIL import Image, ImageDraw
im = Image.open(frame); x, y, w, h = r["rect"]
ImageDraw.Draw(im).rectangle([x, y, x+w, y+h], outline=(255,40,40), width=7)
im.save("check.jpg")
```

A box that has latched onto a spinner or a blinking cursor is obvious in one glance and invisible
in the numbers.

## 10. Speech detection and transcript comparison

```bash
python scripts/speech.py SOURCE.mp4
```

```
{"speech": true, "mean_db": -23.8, "peak_db": -0.4,
 "spoken_seconds": 172.4, "total_seconds": 208.0, "coverage": 0.829,
 "windows": [[1.2, 14.8], [16.1, 39.4], ...]}
```

Two measurements, both straight from ffmpeg:

| Measure | Filter | Reading |
|---|---|---|
| Is the track alive? | `volumedetect` | `mean_volume` below about **-70dB** = silent. Verified: a narrated film reads -23.8dB, a screen recording with a muted mic reads -91.0dB |
| Where is the talking? | `silencedetect=noise=-34dB:d=0.6`, inverted | speech windows. A narrated film gives ~25 windows at 83% coverage |

It detects **sound**, not words. Background music or a noisy room reads as speech - glance at the
window pattern before believing it: continuous coverage above ~95% is usually music, speech
breathes.

**The same filter finds the beats inside a finished narration file**, which is how camera moves
get cued (SKILL.md step 5a):

```bash
ffmpeg -i 01.m4a -af "silencedetect=noise=-34dB:d=0.35" -f null - 2>&1 | grep silence_
```

Gaps of ~0.3-0.5s are sentence ends; ~1.0-1.2s is a paragraph break. Read the script alongside
the gap list to map each named region to the second its phrase starts, and start the move there
so it settles as the phrase finishes. Landing a move in the silence of a paragraph break rather
than over a word is the difference between a move that feels motivated and one that interrupts.

**Getting the words.** No speech-to-text exists here. Ask the user for a **Clipchamp** transcript
(import the recording, turn on captions, export). Read it against the scene map and put the
differences in the Brief - never silently re-plan around it. The useful output is a short list of
questions: what the presenter emphasised that the plan compresses, and what the plan keeps that
the presenter never mentioned.

## 11. Persona images

**Extract from the footage first.** There is no face detection in this environment - no cv2, dlib,
mediapipe or face_recognition, and they cannot be installed. The method is manual, and it works:

```bash
ffmpeg -ss 186.5 -i SOURCE.mp4 -frames:v 1 working/persona_raw.png   # a frame with the face
```

View it, read off the face box, crop and round it:

```python
from PIL import Image, ImageDraw
im = Image.open("working/persona_raw.png").convert("RGB")
f = im.crop((x, y, x + s, y + s)).resize((256, 256), Image.LANCZOS)   # s = square side
m = Image.new("L", (256, 256), 0); ImageDraw.Draw(m).ellipse((0, 0, 255, 255), fill=255)
out = Image.new("RGBA", (256, 256), (0, 0, 0, 0)); out.paste(f, (0, 0), m)
out.save("working/fx/persona_face.png")
```

Look at the result and re-crop - eyes about a third down, a little air above the head. Two or
three iterations is normal. Sources that have worked in practice: a closing photo slide, a Teams
participant tile, and a stock banner already inside the customer's deck.

**Generating one instead.** When the footage holds no face and the colleague has none, generate
it. Tested prompt shape:

> Photorealistic corporate headshot of a [age] [description], a [role], at a desk in a bright
> modern office with a softly blurred window behind. [Clothing]. Calm, professional expression.
> Natural skin texture with visible pores, soft daylight from the left, shallow depth of field.
> Framed mid-chest up, head centred, even space around the head so it can be cropped to a circle
> without clipping. Corporate photography, not glossy stock.

The framing clause matters - a tightly framed generation clips at the circle mask. Square
orientation, medium quality, then crop with the code above. For a set of personas, repeat one
style descriptor verbatim in every prompt so they look like one photo shoot.

**Do not** pull persona photos from an image web search - those results are not reliably licensed
for customer-facing material.

## 12. Teams recordings: stage crop and share detection

**Find the shared window by brightness profile.** The app window is bright, the letterbox around
it is near-black. Take a frame from the middle of the share and read the extremes:

```python
im = np.asarray(Image.open("frame.png").convert("L"), dtype=float)
col, row = im.mean(axis=0), im.mean(axis=1)
l = min(i for i in range(1920) if col[i] > 120)
r = max(i for i in range(1920) if col[i] > 120)
t = min(i for i in range(1080) if row[i] > 120)
b = max(i for i in range(1080) if row[i] > 120)
print(l, t, r - l + 1, b - t + 1, (r - l + 1) / (b - t + 1))
```

A recent 1920x1080 Teams recording gave **1670x944 at offset 0,46** - aspect 1.769, near enough
16:9 to rescale with no letterboxing. Then every segment is cut with:

```bash
-vf "crop=1670:944:0:46,scale=1920:1080:flags=lanczos,fps=30"
```

Keep `fps=30` in the chain - Teams recordings are often 16fps and any `zoompan` move after this
point depends on it (see the SKILL step 3b note).

**Find where the share starts and stops.** Sample the stage edge once per second and threshold
it; gallery view and shared screen separate cleanly:

```bash
ffmpeg -loglevel error -i SRC.mp4 -vf "fps=1,crop=260:200:1660:400,scale=130:100" -q:v 6 sec/s_%04d.jpg
```

```python
v = np.array([np.asarray(Image.open(f).convert("L"), dtype=float)[:, 0:2].mean()
              for f in sorted(glob.glob("sec/s_*.jpg"))])
shared = v > 170          # then find the runs
```

One recording resolved to: gallery 0:00-1:17, **shared 1:18-14:59**, gallery 15:00-15:34.

**Scan the whole recording for on-screen errors** by colour-matching the error icon, rather than
hoping a sampled frame catches one:

```bash
ffmpeg -loglevel error -i SRC.mp4 -vf "fps=1,crop=340:130:1290:330" -q:v 4 toast/t_%04d.jpg
```

```python
r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
if ((r > 140) & (g < 90) & (b < 90)).sum() > 12:  print(i)   # seconds carrying the error
```

Run it over the error's own region (a toast top-right, a banner across the builder) and it
returns exact in/out seconds. This found a 9s failure banner that frame sampling had missed
entirely, and pinned a toast to a 5s window. Cut those windows; do not blur them.

## 13. Opening sequence: title, persona intro, badge

Three beats. The first two are built cards carrying very slow pushes, the third is the badge of
section 4 over the first real scene.

```python
# a card with a slow push - note fps BEFORE the move
vf = f"scale=1920:1080,fps=30,{moves.push((300,180,1320,743), 5.4)},format=yuv420p"
subprocess.run(f'ffmpeg -loglevel error -y -loop 1 -framerate 30 -t {D+0.3} -i card.png '
               f'-vf "{vf}" -t {D} -r 30 -c:v libx264 -preset medium -crf 20 out.mp4', shell=True)
```

Render `-t D+0.3` and trim to `-t D` so the move never runs out of frames at the tail.

**Set the RATE, derive the magnification.** A card push is specified by total magnification but
judged by speed, so two cards of different lengths need different magnifications to look like the
same move. `CARD_RATE = 1.009` per second is the tested drift for a full-frame card:

```python
import moves
D = 13.97
Z = moves.rate_to_Z(moves.CARD_RATE, D)        # 1.009 ** 13.97 = 1.133
rect = moves.focus((415, 535), Z)              # -> (0, 59, 1694, 953)
vf = f"scale=1920:1080,fps=30,{moves.push(rect, D - 0.1)},format=yuv420p"
```

Measured on the two opening cards before this was fixed:

| Card | Written as | Actual rate | Looked like |
|---|---|---|---|
| Title | 1.455x over 5.4s | **1.072/s** | a shove |
| Persona | 1.129x over 13.5s | **1.009/s** | a drift |

Same notation, eight times the speed. Always compute from the rate.

**`focus(point, Z)` - never hand-write a card's rect.** It centres the rect on a focal point and
clamps it inside the frame. A hand-written rect goes wrong in two ways that look identical on
screen, and both read as "the subject is drifting out of shot":

| Fault | Symptom |
|---|---|
| The rect happens to be centred (`fx=0.5`) | the push tracks to the middle of the slide, so an off-centre subject slides away from it |
| The anchor falls outside 0-1 | the crop window runs off the edge of the frame; motion is wrong for the whole move |

Both were present in the persona card: `fx=0.50`, `fy=1.45`. With `focus()` aimed at the
portrait's centre the anchor became `fx=0.00, fy=0.46` and the portrait's centre moved **12px in
14 seconds** - pinned, growing in place.

**Verify it numerically rather than by eye.** Grab the first and last frames, threshold, and
compare the subject's bounding box:

```python
a = np.asarray(Image.open(frame).convert("L"), dtype=float) > 60
lm = a[:, :900]                                  # left half: isolate the portrait
c = np.where(lm.any(axis=0))[0]; r = np.where(lm.any(axis=1))[0]
print((c.min() + c.max()) // 2, (r.min() + r.max()) // 2)
```

A centre that holds within ~20px is anchored. Also check the LAST frame for clipping - a push
crops from the edges, so text near the right or bottom can leave the frame:

```python
print("right margin", 1919 - np.where(a.any(axis=0))[0].max())
```

**The persona intro card** carries the photo left, name/role/organisation right, a rule, then
three challenge lines - what this person is up against, before any product appears:

```python
PS, PX, PY = 470, 180, 300
s = int(ph * 0.72); px = (pw - s) // 2
p = src.crop((px, int(ph*0.02), px + s, int(ph*0.02) + s)).resize((PS, PS), Image.LANCZOS)
m = Image.new("L", (PS*4, PS*4), 0)
ImageDraw.Draw(m).rounded_rectangle([0, 0, PS*4-1, PS*4-1], radius=60, fill=255)
p.putalpha(m.resize((PS, PS), Image.LANCZOS))
```

Split the opening narration at its first natural pause to cover both cards - `silencedetect` on
the clip gives the split point (see SKILL step 5a).

## 14. Trimming narration clips

See the SKILL step 5a note - the rule is `atrim` after decode, never an input-side `-t`, because
AAC priming delay shifts a container-level cut by ~50ms and leaks the next syllable.

```bash
ffmpeg -y -i TQ06.m4a -af \
  "atrim=0:10.15,asetpts=N/SR/TB,afade=t=out:st=10.02:d=0.13,adelay=500|500,apad,\
loudnorm=I=-16:TP=-1.5:LRA=11" -t 11.00 -ar 48000 -ac 2 -c:a pcm_s16le b06.wav
```

Confirm the trim landed by re-reading the result - the tail should be silence, not a quiet
syllable:

```bash
ffmpeg -i b06.wav -af "silencedetect=noise=-40dB:d=0.15" -f null - 2>&1 | grep silence_
```

Build the narration track as one block per scene, each block = clip length + ~0.8s, then concat
the blocks and the segments separately and mux once. Video and audio totals should agree within
about 100ms; if they do not, a block duration and its segment have drifted apart.

## 14a. Gating narration clips (narration_qa.py)

**Run this on every clip before it enters a film, and on the finished film afterwards.**

```bash
python scripts/narration_qa.py narration/*.m4a
python scripts/narration_qa.py output/FILM.mp4
```

It cannot read words - there is no speech-to-text here - but the two faults that keep reaching
delivered films both have an acoustic signature:

| Fault | How it is found |
|---|---|
| **Repeat** - a phrase spoken twice back to back | two nearby windows of speech whose 8-band log spectra match far more closely than ordinary speech does |
| **Clipped tail** - the take ends mid-word | speech energy hard against the end of the file, with less than 0.25s of silence |

**The thresholds are measured - and the margin is NARROW. Read this before trusting a verdict.**
Distance is normalised spectral RMS over a 0.55s window:

| Band | Meaning | Evidence |
|---|---|---|
| < 0.112 | very likely the same phrase twice | both confirmed stutters measured **0.108** |
| 0.112 - 0.17 | report it, let the user listen | legitimate parallel phrasing measured **0.12** and 0.155 |
| > 0.17 | ordinary speech | the remaining clips of a thirteen-clip set |

**It cannot tell an accidental stutter from deliberate parallel phrasing.** A script line reading
"sales data, service data" scored 0.12 - a hair above two genuine stutters at 0.108. That is a
margin of 0.004 and it is not a safe margin. So a FAIL means **"a human must listen to this"**,
never "this is broken" - report the timestamp and ask, do not declare.

**Always measure post-normalisation.** `_pcm()` decodes through `loudnorm` because that is what
the film's blocks carry, and normalisation compresses dynamics enough to move a score by ~0.01 -
which, at this margin, flips a verdict. Measuring the raw clip one way and the assembled block
another makes the gate disagree with itself; one clip scored 0.12 raw and 0.114 assembled.

Validated across thirteen clips: it flagged the two with the known stutter at 0.108, put two
legitimate parallel phrasings in the listen band, and passed the rest.

**No head-silence check.** Clipchamp exports start speech at ~0.06s as a matter of course, so
flagging a short head produces nothing but noise.

**A FAIL means re-record, never trim.** See SKILL step 5a - in-clip surgery on speech is banned,
and the reason is in the history: three failed trims across three renders, then one re-record
that worked. Gate the re-record too; a fresh take of the same line carried the identical stutter
and tripped the check at the same 0.111.

Tuning knobs are at the top of the script: `WIN` (phrase length), `REPEAT_FAIL`, `REPEAT_WARN`,
`SPEECH_DB`, `EDGE_SILENCE`.

## 15. Transitions

`xfade` is present in this build with all 58 transitions. Only three belong in a business demo:
`fadeblack`, `fadewhite`, `fade`.

```bash
ffmpeg -y -i a.mp4 -i b.mp4 -filter_complex \
  "[0:v][1:v]xfade=transition=fadeblack:duration=1.2:offset=OFF,format=yuv420p" \
  -an -c:v libx264 -crf 20 -pix_fmt yuv420p out.mp4
```

`offset` is **where the transition STARTS in the first clip**, so it is `len(a) - duration`. Get
it wrong and the transition either clips the end of A or starts before A has finished.

**Three measured facts:**

| Fact | Measurement |
|---|---|
| The pair gets SHORTER by `duration` | two 4.0s clips + `duration=0.8` -> exactly 7.20s |
| `fadeblack` at 0.8s never truly blacks | darkest frame luma 10.3, and it passes through instantly |
| `fadeblack` at 1.4s does | darkest frame luma **1.8** - a real fade to black |

So: **1.2-1.5s for `fadeblack`/`fadewhite`, 0.5-0.8s for a plain `fade`.**

**Both inputs must already match** - same width, height, fps, SAR and pixel format. `xfade`
silently misbehaves or fails outright otherwise, which is the main reason b-roll needs
normalising first (section 16).

**Chaining several.** Each `xfade` takes two streams, so a chain needs each offset computed
against the RUNNING total, which has already been shortened by every earlier transition:

```python
off = 0.0
for i, d in enumerate(durations[:-1]):
    off += d - D                 # D = transition duration
    print(f"transition {i}: offset={off:.2f}")
```

If only two or three scenes carry transitions, it is simpler and safer to concat the untransitioned
runs first and `xfade` the handful of joins.

**Alternative: a held black gap.** When you want a pause rather than a blend - a real beat between
two halves of a film - fade A out, insert black, fade B in. That gives a hold on black that
`xfade` cannot, because `xfade` always crosses between two moving pictures.

```bash
ffmpeg -y -f lavfi -i color=c=black:s=1920x1080:r=30:d=1.0 -c:v libx264 -crf 20 -pix_fmt yuv420p black.mp4
```

Then `fade=t=out` on A's tail, `fade=t=in` on B's head, and concat A + black + B. Unlike `xfade`,
this ADDS the black clip's length rather than consuming it.

## 16. B-roll

Establishing and atmosphere footage. Normalise it before it touches the timeline - supplied clips
are rarely the demo's shape, frame rate or sample aspect ratio, and both `concat` and `xfade`
require a match.

**Fill the frame (default).** Scales until the frame is covered, then trims the overspill. Right
for wide cinematic b-roll going into a 16:9 film - a 2.39:1 skyline loses a little left and right
and fills the screen:

```bash
ffmpeg -y -i broll.mp4 -vf \
  "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,\
setsar=1,fps=30,format=yuv420p" -an -c:v libx264 -crf 20 broll_norm.mp4
```

**Letterbox instead** only when the whole frame matters (a wide diagram, a full product shot) -
black bars read as a mistake on atmosphere footage:

```bash
-vf "scale=1920:1080:force_original_aspect_ratio=decrease,\
pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30,format=yuv420p"
```

`setsar=1` is not optional - stock footage often carries a non-square sample aspect ratio, which
survives scaling and stretches the picture once it is concatenated. `-an` is not optional either:
b-roll usually carries music or ambience that will fight the narration.

Verified end to end: a 1280x536 @ 25fps clip normalised to 1280x720 @ 30fps and `xfade`d into a
demo segment produced exactly the expected total (3.0 + 4.0 - 1.0 = 6.0s).

**Where it goes:** opening establishing shot before the title card, a bridge at a genuine section
break, a closing shot under the value card. Two to four seconds each, three placements at most.

**Subject follows the customer's industry** - a skyline for a bank, a plant floor for a
manufacturer, a ward for a health provider, a depot for logistics, a campus for education. Ask
what the colleague has rather than assuming a sector.

**Check the clip is not branded.** Supplied b-roll sometimes carries a watermark, a logo on a
building, or a recognisable competitor's premises. Look at it before using it.

## 17. Multiple personas

Drive every card and badge from one record per person, so a persona looks identical everywhere:

```python
PERSONAS = {
    "alan":  dict(name="Alan Reid",    role="Customer Service Operations",
                  org="Contoso Bank", photo="working/alan.png",  accent=(0, 160, 220)),
    "priya": dict(name="Priya Nandra", role="Credit Risk",
                  org="Contoso Bank", photo="working/priya.png", accent=(230, 140, 0)),
}
```

The **accent colour** is the cheap distinguishing device - it runs down the badge edge, under the
introduction card's name, and on that persona's block in the closing value card, so the audience
learns "blue is Alan, amber is Priya" without being told. Keep the rest of the design identical.

Generate missing photographs with the SAME style sentence for every persona (section 11), or the
set looks like it came from three different stock libraries.

### Swapping the badge inside one scene

Two overlays on one segment, each with its own alpha fade and its own `enable` window. Tested and
verified frame by frame - badge A present, clean frame between, badge B present:

```bash
ffmpeg -y -i scene.mp4 \
  -loop 1 -framerate 30 -t 12 -i badge_alan.png \
  -loop 1 -framerate 30 -t 12 -i badge_priya.png \
  -filter_complex "\
[1:v]fade=t=in:st=0.3:d=0.5:alpha=1,fade=t=out:st=4.5:d=0.6:alpha=1[b1];\
[2:v]fade=t=in:st=6.0:d=0.5:alpha=1,fade=t=out:st=10.2:d=0.6:alpha=1[b2];\
[0:v][b1]overlay=0:0:enable='between(t,0.3,5.1)'[v1];\
[v1][b2]overlay=0:0:enable='between(t,6.0,10.8)',format=yuv420p[v]" \
  -map "[v]" -an -t 12 -r 30 -c:v libx264 -crf 20 -pix_fmt yuv420p out.mp4
```

Two things to get right:

- **The `enable` window must cover the whole fade**, including its tail - `st + d` on the fade-out,
  not just `st`. A window that closes early cuts the badge off mid-fade, which reads as a glitch.
- **The `fade` timings are in the OVERLAY's own timeline**, which starts at 0 for each `-loop 1`
  input, and they happen to coincide with the scene's timeline here because both start together.
  If you trim the scene later, both the fades and the windows move.

Cue the swap to the narration, not to the picture: the badge changes on the phrase that hands
over, which step 5a's `silencedetect` pass gives you.

### The compact mid-film introduction card

A persona arriving mid-film gets a smaller card than the opening one - the audience knows the
format by now. Photo left at ~300px, name, role, and ONE line of why they are here. 4-6 seconds,
under the narration line that introduces them, with the same very slow push as the opening cards.

### Closing value with several personas

One block per persona, each headed with their name in their accent colour, then a shared line
for the organisation:

```python
# two personas side by side; three stack vertically
for i, key in enumerate(order):
    p = PERSONAS[key]
    x = 200 + i * 780
    d.text((x, 360), p["name"], font=fb(40), fill=p["accent"])
    for j, line in enumerate(value_lines[key]):
        d.text((x, 430 + j * 76), line, font=fr(32), fill=(224, 231, 240))
d.text((200, 820), shared_line, font=fb(36), fill=(255, 255, 255))
```

Do not merge them into one summary. The argument of a multi-persona demo is that one system served
different people differently, and a merged list is exactly the evidence of that being thrown away.

## 18. Audio and timing traps (measured)

- `loudnorm` inside a `-filter_complex` graph produces non-monotonic DTS and the mux fails
  ("Application provided invalid, non monotonically increasing dts"). Normalise each clip to WAV
  in a separate pass first, then delay and mix the WAVs.
- `apad` with `-shortest` hangs the muxer indefinitely — the file reaches full size and never
  finalises, so it has no `moov` atom and will not open. Use `apad=whole_dur=<seconds>` to pad to
  a known length instead, then mux without `-shortest`.
- `loudnorm`'s `TP` parameter is limited to the range [-9, 0]; a lower value is rejected outright.
  To place a music bed well below that ceiling, set the `I` target low and leave `TP` at -9.
- `tpad=stop_mode=clone:stop_duration=N` appended after the scale/fps chain freezes the final
  frame for N seconds. Use it for read time; it beats slowing the whole shot, which judders.
- To extend a finished, chip-overlaid picture by a few seconds of black, render the tail
  separately and `concat -c copy` it on rather than re-running the overlay pass. Identical
  encoder settings are required for the stream copy to join cleanly. (An overlay pass over a
  3-minute 1080p film took ~15 minutes; the concat takes seconds.)
- `-stream_loop N` before `-i` on a music track loops it to cover a film longer than the track.

## 19. HTML/CSS overlays rendered to transparent PNG (preferred for cards and badges)

Preferred over drawing cards with an imaging library: real typography, a translucent panel that
lets the footage through, and a whole set restyled from a handful of CSS custom properties.
Applies to title cards, persona introduction cards, lower-third overlays, corner badges, caption
chips and the closing value card.

**Requires a real browser engine.** Container queries and `backdrop-filter` are modern CSS; a
document converter (LibreOffice and similar) will not honour them and may not export PNG at all.
Check for a headless Chromium (Playwright/Puppeteer) or the environment's own HTML-to-image path
BEFORE choosing this route. With no renderer, build the same SPEC with the imaging library — the
token block, translucent panel, accent bar and type hierarchy are what make it look professional.

### Lower-third persona overlay

```html
<!DOCTYPE html><html lang="en-GB"><head><meta charset="utf-8"><style>
  :root{
    --brand:#006A4D; --brand-deep:#00382A; --brand-light:#6ECDB1;
    --panel-bg:rgba(0,56,42,.72); --rule:rgba(110,205,177,.85); --text:#FFF;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;background:transparent}          /* transparent = compositable */
  body{font-family:"Segoe UI",Frutiger,"Helvetica Neue",Arial,sans-serif;
       -webkit-font-smoothing:antialiased}
  .stage{position:relative;width:100vw;height:56.25vw;           /* locked 16:9 */
         max-height:100vh;max-width:177.778vh;margin-inline:auto;
         background:transparent;container-type:size}
  .lower-third{position:absolute;inset-block-end:0;inset-inline:0;block-size:33.333%;
               display:flex;flex-direction:column;justify-content:flex-end;
               padding-inline:6cqw;padding-block-end:5cqh}
  .panel{background:var(--panel-bg);backdrop-filter:blur(6px);
         border-inline-start:.55cqw solid var(--brand-light);border-radius:.4cqw;
         padding:3.2cqh 3.4cqw;max-inline-size:62%;
         box-shadow:0 .6cqh 2.4cqh rgba(0,0,0,.28)}
  .name{margin:0;font-size:6.2cqh;line-height:1.05;font-weight:700;
        letter-spacing:-.01em;color:var(--text)}
  .role{margin:1.4cqh 0 0;font-size:2.7cqh;font-weight:600;letter-spacing:.04em;
        color:var(--brand-light)}
  .divider{inline-size:100%;block-size:.28cqh;background:var(--rule);opacity:.55;
           margin-block:2.2cqh;border:0}
  .objective-label{margin:0 0 .9cqh;font-size:1.9cqh;font-weight:700;letter-spacing:.16em;
                   text-transform:uppercase;color:var(--brand-light)}
  .objective{margin:0;font-size:2.9cqh;line-height:1.35;color:var(--text)}
</style></head><body>
  <div class="stage"><div class="lower-third"><div class="panel">
    <h1 class="name">Ben McPhee</h1>
    <p class="role">Lending manager &middot; case owner</p>
    <hr class="divider">
    <p class="objective-label">Objective</p>
    <p class="objective">To spend less time re-keying information, chasing cases and doing
      admin — and to provide a better client lending experience.</p>
  </div></div></div>
</body></html>
```

### Corner badge (circular photo + name + role)

Same token block. The photo is cropped to a circle in CSS, so no image pre-processing:

```css
.badge{position:absolute;inset-block-start:4cqh;inset-inline-start:4cqw;
       display:flex;align-items:center;gap:2cqw;
       background:var(--panel-bg);backdrop-filter:blur(6px);
       border-inline-start:.5cqw solid var(--brand-light);border-radius:.6cqw;
       padding:1.6cqh 2.6cqw 1.6cqh 2cqw}
.badge img{inline-size:9cqh;block-size:9cqh;border-radius:50%;object-fit:cover;
           border:.35cqh solid var(--brand-light)}
.badge .n{font-size:3.4cqh;font-weight:700;color:var(--text);line-height:1}
.badge .r{font-size:2.2cqh;color:var(--brand-light);line-height:1.2}
```

### Render and composite

Render at the film's exact frame size with a transparent background, then overlay:

```bash
# render (headless Chromium, via whatever driver the environment provides):
#   viewport = film frame size, omitBackground = true, output PNG

ffmpeg -y -i scene.mp4 -loop 1 -framerate 30 -t 6 -i persona-ben.png \
  -filter_complex "[1:v]format=rgba,fade=t=in:st=0:d=0.45:alpha=1,\
fade=t=out:st=5.55:d=0.45:alpha=1[o];[0:v][o]overlay=0:0[v]" \
  -map "[v]" -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p out.mp4
```

Keep one HTML file per persona and per card (`persona-<name>.html`, `title.html`, `value.html`).
A brand or job-title change is then one token or one line, not a re-drawn image.

## 20. "Under the bonnet" panel layout (optional, default OFF - SKILL.md step 2g)

Tested on a six-film 1920x1080 set. Geometry, in pixels:

```python
W, H = 1920, 1080
TOPH = 162                      # top band height (only if the top panel is ON)
FX, FY, FW, FH = 0, TOPH, 1920, 918          # footage area under the band: full width, no side crop
# panel block (bottom and/or right panel visible): footage shrinks into a window
VX, VY = 18, 172
BH = 262; BY = H - 12 - BH                   # bottom panel 806..1068
VH = BY - 12 - VY                            # 622
VW = round(VH * FW / FH)                     # 1301 - aspect preserved, never cropped
RX = VX + VW + 16; RW = W - 18 - RX          # right panel fills the remaining width
```

**Fitting footage under a band without cropping its sides.** Crop top and bottom only, or scale:
an ultrawide capture `crop=2250:1080:135:0,scale=1920:918`; a letterboxed 16:9 capture
`crop=1920:918:0:81` (its letterbox bars are what is removed); a phone capture is padded, never
cropped. With the top panel OFF, the footage is plain 1920x1080 and the panel block uses the same
window geometry with VY moved up.

**The pull-out into a panel block.** Animate the footage from its full-frame rectangle to the
window (VX, VY, VW, VH) over ~1.4s with an ease, render the panels as a static PNG background
underneath, and reverse over ~1.1s at the end of the block. During the block the window shows a
FROZEN frame - pick its source time deliberately (a content-rich screen), passing it explicitly
rather than defaulting to the last frame of the previous segment.

**Revealing chips with the narration.** Split the block's clip at its sentence breaks (the n
longest pauses below -34dB - see s18) and step the panel state at each break: bottom panel in,
then zones lit one at a time, then the right panel. Allow ~2.5s of reading hold after each reveal
before the next sentence's state lands.

**Top band introduction.** One clip at the head of each film; highlight the whole band, then
each section in turn on each sentence break (journey title, requirements + people, "what you're
seeing"). Section x-ranges in the tested layout: 10-462, 470-1405, 1410-1910.

**Draw panels with the same rules as cards** (s19): text labels on every coloured element, panel
sizes from rendered ink, contrast checked on the dimmed (unlit) chips as well as the lit ones.

## 21. Cutting pointer-waving out of a segment (SKILL.md step 3a)

Classify the source at 4fps on a small greyscale copy, then plan the pieces. Tested on 1080p
ultrawide and 16:9 Dynamics 365 captures.

```python
import subprocess, numpy as np
def activity(src, crop, a, b, fps=4, W=480, H=230):
    raw = subprocess.run(["ffmpeg","-nostdin","-loglevel","error","-ss",str(a),"-to",str(b),"-i",src,
        "-vf",f"{crop},fps={fps},scale={W}:{H},format=gray","-f","rawvideo","-"],
        capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.int16)
    labs = []
    for i in range(1, len(fr)):
        d = np.abs(fr[i] - fr[i-1]) > 24
        n = int(d.sum()); frac = n / (W*H)
        if n == 0: labs.append("s"); continue
        ys, xs = np.where(d)
        bbox = (np.ptp(xs)+1) * (np.ptp(ys)+1) / (W*H)
        labs.append("c" if frac > 0.004 or (bbox > 0.08 and frac > 0.0015)
                    else "m" if frac > 0.00005 else "s")
    return labs        # one label per 0.25s: c = content change, m = pointer only, s = static
```

Planner rules, in order (T = the narrated segment length, dt = 0.25):

1. `keep` everything; then mark dropped every 8-step window with `count("m") >= 6` and
   `count("c") <= 1`. Always keep the first 2 and last 4 steps.
2. While kept length > `T * 1.3`: drop the longest remaining all-"m" runs (3+ steps), then trim
   "s" runs to 6 steps (1.5s).
3. If kept length < `(T - 4) * 0.9`: restore dropped steps in order of fewest "m" labels in the
   surrounding +/-1s; then close single-step gaps.
4. Pieces = contiguous kept runs; discard pieces under 0.75s (except first and last).
   `sp = clamp(kept / T, 0.85, 1.8)`. Spare time `T - kept/sp` becomes holds of up to 3s each on the
   longest pieces, remainder on the last piece.

Render each piece with `trim=start:end,setpts=(PTS-STARTPTS)/sp` plus
`tpad=stop_mode=clone:stop_duration=hold`, `concat` the pieces, and check the segment's
duration equals T. Print the plan (pieces, sp, total hold) for every segment before rendering -
a segment whose plan is mostly hold is about to look like stills again.
