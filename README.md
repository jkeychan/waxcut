# waxcut

[![PyPI](https://img.shields.io/pypi/v/waxcut.svg)](https://pypi.org/project/waxcut/)
[![CI](https://github.com/jkeychan/waxcut/actions/workflows/ci.yml/badge.svg)](https://github.com/jkeychan/waxcut/actions/workflows/ci.yml)
[![Fuzzing](https://github.com/jkeychan/waxcut/actions/workflows/cflite_pr.yml/badge.svg)](https://github.com/jkeychan/waxcut/actions/workflows/cflite_pr.yml)
[![Docs](https://api.netlify.com/api/v1/badges/1bee32bd-9b6d-46f3-b798-e6cc28226a0d/deploy-status)](https://waxcut.netlify.app/)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/jkeychan/waxcut/badge)](https://scorecard.dev/viewer/?uri=github.com/jkeychan/waxcut)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/13947/badge)](https://www.bestpractices.dev/projects/13947)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/waxcut.svg)](pyproject.toml)

Frame-accurate, lossless MP3 splitting and duration parsing in pure Python —
no ffmpeg, no subprocess, no decode step.

Cuts are made by parsing the file's own MPEG frame headers and byte-copying
whole frames: output is bit-identical to the source, just shorter.

## Install

```bash
pip install waxcut
# or
uv add waxcut
```

## Usage

```python
from pathlib import Path
from waxcut import load_audio_stream, frame_index_at, slice_bytes

stream = load_audio_stream(Path("song.mp3"))
print(f"{stream.playable_duration_ms / 1000:.1f}s")

# Split at the 90-second mark
cut_at = frame_index_at(stream.frames, target_ms=90_000)
first_half = slice_bytes(stream.data, stream.frames, 0, cut_at)
second_half = slice_bytes(stream.data, stream.frames, cut_at, len(stream.frames))

Path("part1.mp3").write_bytes(first_half)
Path("part2.mp3").write_bytes(second_half)
```

## Why not ffmpeg or a decode/re-encode library?

MP3 frames are self-describing, so their boundaries can be found directly
from the byte stream — no decode step, no re-encode step, no external
binary to shell out to.

waxcut also handles the parts that make naive frame-splitting subtly wrong:

- Skips leading `ID3v2` tags when scanning for the first frame.
- Excludes the `Xing`/`Info`/`VBRI` VBR header frame — encoder metadata, not
  audio, and including it corrupts both output and duration.
- Parses LAME's gapless delay/padding extension, so reported duration
  matches what a real player shows, not just the raw frame count.

Duration parsing is cross-validated against
[mutagen](https://github.com/quodlibet/mutagen)'s independent implementation
to the millisecond (see [Testing](#testing)).

## Scope

Parses **MPEG-1/2/2.5 Audio Layer III** — what "MP3" actually means. Layer
I/II frames raise `UnsupportedMp3Error` rather than being silently
mishandled, since virtually no real-world "MP3" file uses them.

## Testing

```bash
uv sync
uv run pytest tests/ -v
```

Validated against mutagen's independent parser (duration must match
exactly, including LAME gapless delay/padding) across CBR/VBR, mono/stereo,
and multiple encoder tags. Where `ffmpeg`/`ffprobe` are available, every
split output is independently decoded to confirm it's valid. Fuzzed
continuously with [ClusterFuzzLite](.clusterfuzzlite/).

## Contributing

Bug reports and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md)
for the dev setup and PR process. Report security vulnerabilities per
[SECURITY.md](SECURITY.md) rather than as public issues.

## License

Apache-2.0 — see [LICENSE](LICENSE).
