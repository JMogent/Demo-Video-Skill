"""Post-process generated narration clips: slow them, open up the pauses, gate them.

host-PodcastGenerate is an AGENT tool - it cannot be called from bash. The agent
generates one raw MP3 per scene (single_host, voice en-GB-AdaMultilingualNeural),
then runs this script over those files.

Why: raw generated speech measured ~178-181 words/minute (up to ~205 on long
sentences) - too fast for demo narration - and the tool has no rate or pause
parameter. SSML in the turn text is read aloud, not honoured.

Default treatment ("0.85x with added pauses", PROVISIONAL pending the user's
listening confirmation):
  1. silencedetect (n=-35dB, d=0.25) finds the natural pauses in the raw clip
  2. the clip is cut into phrases at the MIDPOINT of each detected silence
  3. each phrase gets atempo=0.85 (pitch preserved)
  4. after each phrase, extra silence: --short-pause (0.35 s) if the original gap
     was < --gap-threshold (0.5 s, a comma/phrase break), --long-pause (0.6 s) if
     >= it (a sentence break), and --tail (0.5 s) after the final phrase
  5. concat filter, 24 kHz mono, AAC 128k

Measured on a 44-word Ada test: plain 0.85x = 17.55 s (~150 wpm); with pauses
= 19.79 s (~133 wpm); phrase breaks ~0.66-0.86 s, sentence breaks ~1.24-1.29 s;
narration_qa.py OK (head 0.10 s, tail 0.66 s).

--no-pauses gives plain 0.85x: atempo + apad (--pad, 0.4 s; without padding the
slowed tail measured 0.16-0.17 s and failed narration_qa's tail check).

Output files are named by the scene's opening timecode: pass --names 01_000 02_012
(one per input, in order), otherwise the input's stem is used. Each output is
then run through narration_qa.py. If --scripts gives one plain-text file per
input, words/minute is reported against the delivered duration.

Usage:
  python narrate.py raw/s1.mp3 raw/s2.mp3 --names 01_000 02_012 \
      --scripts txt/s1.txt txt/s2.txt --outdir working/narration
ffprobe is not available in this environment; durations come from ffmpeg output.
"""
import argparse, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import narration_qa  # noqa: E402

SR = 24000


def duration(path):
    """Duration in seconds, parsed from ffmpeg's own output (no ffprobe here)."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-f", "null", "-"],
                       capture_output=True, text=True)
    hits = re.findall(r"time=(\d+):(\d+):([\d.]+)", p.stderr)
    if hits:
        h, m, s = hits[-1]
    else:
        mm = re.search(r"Duration: (\d+):(\d+):([\d.]+)", p.stderr)
        if not mm:
            raise RuntimeError(f"cannot read duration of {path}")
        h, m, s = mm.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def silences(path, noise_db=-35, min_dur=0.25):
    """Detected silences as (start, end) pairs."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af",
                        f"silencedetect=n={noise_db}dB:d={min_dur}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    return list(zip(starts, ends))


def plan(path, a):
    """Phrase boundaries and the extra pause to add after each phrase."""
    total = duration(path)
    cuts = []                                   # (cut_time, original_gap)
    for s, e in silences(path, a.noise_db, a.min_silence):
        if s <= 0.05 or e >= total - 0.05:      # leading/trailing room tone, not a break
            continue
        cuts.append(((s + e) / 2, e - s))
    bounds = [0.0] + [c for c, _ in cuts] + [total]
    pauses = [a.short_pause if g < a.gap_threshold else a.long_pause for _, g in cuts] + [a.tail]
    return [(bounds[i], bounds[i + 1], pauses[i]) for i in range(len(bounds) - 1)]


def process(src, dst, a):
    if a.no_pauses:
        af = f"atempo={a.tempo},apad=pad_dur={a.pad}"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", af,
               "-ar", str(SR), "-ac", "1", "-c:a", "aac", "-b:a", "128k", dst]
        subprocess.run(cmd, check=True)
        return 0
    segs = plan(src, a)
    parts, labels = [], []
    for i, (t0, t1, pad) in enumerate(segs):
        parts.append(f"[0:a]atrim=start={t0:.3f}:end={t1:.3f},asetpts=PTS-STARTPTS,"
                     f"aresample={SR},atempo={a.tempo},apad=pad_dur={pad}[p{i}]")
        labels.append(f"[p{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segs)}:v=0:a=1[out]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-filter_complex", graph,
           "-map", "[out]", "-ar", str(SR), "-ac", "1", "-c:a", "aac", "-b:a", "128k", dst]
    subprocess.run(cmd, check=True)
    return len(segs)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="+", help="raw per-scene MP3s from PodcastGenerate")
    ap.add_argument("--names", nargs="*", help="opening-timecode names, e.g. 01_000 02_012")
    ap.add_argument("--scripts", nargs="*", help="plain-text narration per input (for wpm)")
    ap.add_argument("--outdir", default="working/narration")
    ap.add_argument("--tempo", type=float, default=0.85)
    ap.add_argument("--no-pauses", action="store_true", help="plain tempo change, no added pauses")
    ap.add_argument("--pad", type=float, default=0.4, help="tail pad for --no-pauses")
    ap.add_argument("--short-pause", type=float, default=0.35)
    ap.add_argument("--long-pause", type=float, default=0.6)
    ap.add_argument("--tail", type=float, default=0.5)
    ap.add_argument("--gap-threshold", type=float, default=0.5)
    ap.add_argument("--noise-db", type=float, default=-35)
    ap.add_argument("--min-silence", type=float, default=0.25)
    a = ap.parse_args()

    if a.names and len(a.names) != len(a.inputs):
        sys.exit("--names must give one name per input")
    if a.scripts and len(a.scripts) != len(a.inputs):
        sys.exit("--scripts must give one text file per input")
    os.makedirs(a.outdir, exist_ok=True)

    mode = f"{a.tempo}x" + ("" if a.no_pauses else " with added pauses")
    print(f"narrate.py - {mode}\n")
    all_ok = True
    for i, src in enumerate(a.inputs):
        name = a.names[i] if a.names else os.path.splitext(os.path.basename(src))[0]
        dst = os.path.join(a.outdir, f"{name}.m4a")
        n = process(src, dst, a)
        raw, out = duration(src), duration(dst)
        r = narration_qa.check(dst)
        all_ok &= r["ok"]
        line = (f"{'OK  ' if r['ok'] else 'FAIL'} {dst}  raw={raw:.2f}s  out={out:.2f}s"
                f"  head={r['head_silence']} tail={r['tail_silence']}")
        if n:
            line += f"  phrases={n}"
        if a.scripts:
            words = len(open(a.scripts[i], encoding="utf-8").read().split())
            line += f"  words={words}  wpm={words / out * 60:.0f}"
        print(line)
        for f in r["faults"]:
            print("      -", f)
        for t1, t2, d in r["possible_repeats"]:
            print(f"      ? possible repeat {t1}s / {t2}s (distance {d}) - worth a listen")
    print("\nAll clips passed narration_qa." if all_ok else "\nSome clips FAILED - fix before use.")
    print("The words are unverified: the user must listen to every clip.")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
