"""Catch narration defects a listener would hear, without being able to listen.

There is no speech-to-text in this environment, so this cannot read the words.
What it CAN do is find the acoustic signature of the two faults that keep
reaching delivered films:

  repeat   a phrase spoken twice back to back ("already are already are")
  clipped  the take starts or ends mid-word, with no silence to sit in

Both are measurable. A repeat is two adjacent windows of speech whose spectral
shape matches far more closely than ordinary speech ever does; a clipped take
is speech energy hard against the file boundary.

Run it on EVERY clip before it goes into a film, and on the film afterwards.
"""
import subprocess, math
import numpy as np

SR = 16000
HOP = 0.01                      # 10ms frames
WIN = 0.55                      # phrase window - about two short words
# Measured separation is NARROW and this tool cannot tell an accidental stutter
# from deliberate parallel phrasing ("sales data, service data"). Treat both
# bands as "a human must listen", not as a verdict.
REPEAT_FAIL = 0.112             # two confirmed stutters measured 0.108
REPEAT_WARN = 0.17              # legitimate parallel phrasing measured 0.114-0.12
SPEECH_DB = -30                 # a window this loud is speech, not room tone
EDGE_SILENCE = 0.25             # a clean take needs this much quiet at each end


def _pcm(path):
    """Decode to mono 16k, ALWAYS through loudnorm.

    The film's blocks are loudness-normalised, and normalisation compresses
    dynamics enough to move a similarity score by ~0.01 - which straddles the
    threshold. Measuring raw here and normalised there makes the gate
    disagree with itself, so everything is measured post-normalisation.
    """
    p = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path,
                        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                        "-ar", str(SR), "-ac", "1", "-f", "s16le", "-"],
                       capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.int16).astype(float) / 32768


def _features(a):
    """Per-frame loudness (dB) and an 8-band log spectrum."""
    w, hop = 400, int(SR * HOP)
    lo, sp = [], []
    han = np.hanning(w)
    for i in range(0, len(a) - w, hop):
        s = a[i:i + w]
        lo.append(20 * math.log10(np.sqrt((s ** 2).mean()) + 1e-9))
        S = np.abs(np.fft.rfft(s * han, 512))
        sp.append(np.log(np.array([S[b:b + 25].mean() for b in range(0, 200, 25)]) + 1e-8))
    F = np.array(sp)
    F = (F - F.mean(0)) / (F.std(0) + 1e-9)
    return np.array(lo), F


def find_repeats(lo, F, max_gap=0.9):
    """Adjacent, near-identical speech windows = a phrase said twice."""
    L = int(WIN / HOP)
    n = min(len(lo), len(F))
    out = []
    for i in range(0, n - L, 3):
        if lo[i:i + L].mean() <= SPEECH_DB:
            continue
        for j in range(i + L // 2, min(i + int(max_gap / HOP) + L, n - L), 3):
            if lo[j:j + L].mean() <= SPEECH_DB:
                continue
            d = np.linalg.norm(F[i:i + L].ravel() - F[j:j + L].ravel()) / np.sqrt(L * F.shape[1])
            if d < REPEAT_WARN:
                out.append((round(i * HOP, 2), round(j * HOP, 2), round(float(d), 3)))
    # keep the best hit per neighbourhood
    out.sort(key=lambda r: r[2])
    kept = []
    for t1, t2, d in out:
        if any(abs(t1 - a) < 0.6 for a, _, _ in kept):
            continue
        kept.append((t1, t2, d))
    return kept


def edges(lo):
    """Silence at the head and tail, in seconds."""
    sp = np.where(lo > SPEECH_DB)[0]
    if not len(sp):
        return None, None
    return round(sp[0] * HOP, 2), round((len(lo) - 1 - sp[-1]) * HOP, 2)


def check(path, expected_words=None):
    a = _pcm(path)
    lo, F = _features(a)
    dur = len(a) / SR
    head, tail = edges(lo)
    reps = find_repeats(lo, F)
    spoken = float((lo > SPEECH_DB).sum()) * HOP

    faults = []
    hard = [r for r in reps if r[2] < REPEAT_FAIL]
    soft = [r for r in reps if r[2] >= REPEAT_FAIL]
    if hard:
        faults.append(f"REPEAT: phrase at {hard[0][0]}s closely matches {hard[0][1]}s "
                      f"(distance {hard[0][2]}) - LISTEN to this before using the clip")
    # NB: no head check. Clipchamp exports start speech at ~0.06s as a matter of
    # course, so a short head is normal and flagging it only produces noise.
    if tail is not None and tail < EDGE_SILENCE:
        faults.append(f"CLIPPED TAIL: only {tail}s of silence after the last word")
    if expected_words:
        wps = expected_words / spoken if spoken else 0
        if not 1.6 <= wps <= 3.0:
            faults.append(f"PACE: {wps:.2f} words/sec against {expected_words} scripted words "
                          f"- the take may not match the script")

    return {"file": path, "duration": round(dur, 2), "speech_seconds": round(spoken, 2),
            "head_silence": head, "tail_silence": tail,
            "repeats": hard, "possible_repeats": soft,
            "faults": faults, "ok": not faults}


if __name__ == "__main__":
    import sys, json
    for p in sys.argv[1:]:
        r = check(p)
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"{mark} {p}  dur={r['duration']}s head={r['head_silence']} tail={r['tail_silence']}")
        for f in r["faults"]:
            print("      -", f)
        for t1, t2, d in r["possible_repeats"]:
            print(f"      ? possible repeat {t1}s / {t2}s (distance {d}) - worth a listen")
