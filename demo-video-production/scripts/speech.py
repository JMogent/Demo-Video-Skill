"""Detect whether a recording carries spoken commentary, and where.

There is no speech-to-text in this environment, so this answers "is someone
talking, and when" - NOT "what did they say". A transcript has to come from
the user (Clipchamp, Stream, Word) for the content comparison.
"""
import subprocess, re, json


def audio_present(src):
    """Is there an audio stream, and does it carry anything above silence?"""
    p = subprocess.run(["ffmpeg", "-i", src, "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    err = p.stderr
    if "Audio:" not in err:
        return {"has_stream": False, "mean_db": None, "silent": True}
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", err)
    mx = re.search(r"max_volume:\s*(-?[\d.]+) dB", err)
    mean = float(m.group(1)) if m else None
    peak = float(mx.group(1)) if mx else None
    # a truly silent track reports about -91 dB on both
    silent = mean is not None and mean < -70
    return {"has_stream": True, "mean_db": mean, "peak_db": peak, "silent": silent}


def speech_windows(src, thresh_db=-34, min_silence=0.6):
    """Timecoded windows where sound is present, via ffmpeg's silencedetect."""
    p = subprocess.run(
        ["ffmpeg", "-i", src, "-af",
         f"silencedetect=noise={thresh_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", p.stderr)]
    dur = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", p.stderr)
    total = (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))) if dur else None

    # invert the silences to get the speech windows
    windows = []
    cursor = 0.0
    for i, s in enumerate(starts):
        if s > cursor + 0.2:
            windows.append((round(cursor, 1), round(s, 1)))
        cursor = ends[i] if i < len(ends) else (total or s)
    if total and cursor < total - 0.2:
        windows.append((round(cursor, 1), round(total, 1)))
    return windows, total


def analyse(src):
    a = audio_present(src)
    if not a["has_stream"] or a["silent"]:
        return {"speech": False, **a}
    wins, total = speech_windows(src)
    spoken = sum(b - a_ for a_, b in wins)
    return {"speech": spoken > (total or 1) * 0.10,
            "windows": wins, "spoken_seconds": round(spoken, 1),
            "total_seconds": round(total or 0, 1),
            "coverage": round(spoken / total, 3) if total else None, **a}


if __name__ == "__main__":
    import sys
    print(json.dumps(analyse(sys.argv[1]), indent=2)[:2000])
