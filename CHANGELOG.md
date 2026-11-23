# Changelog

Changes to the demo video production skill, newest first. Version numbers follow `MAJOR.MINOR.PATCH`:

- **MAJOR** changes how you work with the skill
- **MINOR** adds new capability
- **PATCH** is a fix or a wording change

## 1.5.0 — 23 November 2026

### Added
- **Motion graphics and music-led films (new step 3e).** The skill now builds animated inserts and whole song-led explainers around real footage, captures or its own outputs: title cards, number cross-outs, word-by-word captions, waveforms from real audio, clocks with real timestamps, process lines, fast credit rolls, cross-outs, sparkles and seasonal backgrounds.
- **New script `scripts/motion.py`.** The motion kit: one theme object, one draw function per effect, a frame reader for footage, push and spotlight for stills, and chunked rendering that resumes after an interrupted session. Needs only Pillow, numpy and ffmpeg. Run `python scripts/motion.py demo test.mp4` to check an environment.
- **Music-led films.** Guidance on taking section timings from the user, a single offset for the second beat of each section, using interludes as content slots, and clearing third-party music.
- **Clipchamp `.webm` captures** are converted to constant-frame-rate .mp4 before use (`python scripts/motion.py webm IN.webm OUT.mp4`).

### Changed
- **Scope.** The skill no longer refuses motion graphics. It still does not do pure animation with no real material, or character animation.
- **HTML/CSS card route.** When no browser engine is available, the skill now points to the motion kit as the ready-made fallback.

## 1.4.0 — 12 November 2026

### Added
- **Generated narration.** Copilot now produces one narration audio file per scene, so you no longer render the narration in Clipchamp and return it.
- **Pronunciation check.** Before the full narration, a short clip says every name and acronym in the film. Wrong names are respelt in the text the voice reads only.
- **Voice choice.** Ada (British English female) is the default. Ollie (British male) and Sonia (British female) are available on request. One voice is used for the whole film.
- **New script `scripts/narrate.py`.** It slows each clip to 0.85x, keeping the pitch, and adds longer pauses between phrases and sentences. It then runs the clip checks and names each file by its scene's start time.
- **Requirements** are now listed at the top of `SKILL.md`.
- **Version number** is now the first line under the title in `SKILL.md`.

### Changed
- **Script word budget** is now about 130 words per minute of film (was 125), to match the generated voice's delivered pace of about 133 words a minute. Provisional until confirmed on a full film.
- **Clipchamp text-to-speech is now the fallback** rather than the default. It's for stubborn mispronunciations, finer pace or emphasis control, a specific voice, or checking the final words through captions.
- **Real customer names** in examples are replaced with fictional ones, ready for public release.

## Earlier versions

Versions before 1.4.0 were shared directly and have no detailed changelog. If you have a copy without a version line under the title in `SKILL.md`, it is older than 1.4.0.
