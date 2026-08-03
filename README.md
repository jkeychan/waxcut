# waxcut

[![CI](https://github.com/jkeychan/waxcut/actions/workflows/ci.yml/badge.svg)](https://github.com/jkeychan/waxcut/actions/workflows/ci.yml)
[![Workflow Security and Linting](https://github.com/jkeychan/waxcut/actions/workflows/zizmor.yml/badge.svg)](https://github.com/jkeychan/waxcut/actions/workflows/zizmor.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/jkeychan/waxcut/badge)](https://scorecard.dev/viewer/?uri=github.com/jkeychan/waxcut)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Frame-accurate, lossless MP3 splitting and duration parsing in pure Python —
no ffmpeg, no subprocess, no decode step.

Cuts are made by parsing the file's own MPEG frame headers and byte-copying
whole frames, so output is bit-identical to the source, just shorter. No
audio decoding happens at all: not on the way in, not on the way out.

## Why

Most MP3-splitting tools shell out to `ffmpeg` or fully decode the file into
PCM before re-encoding. Both work, but both are heavier than the actual
problem requires: MP3 frames are self-describing, so their boundaries can be
located directly from the byte stream and cut without touching the encoded
audio at all. `waxcut` does that — a self-contained frame parser with no
runtime dependencies and no external binary.

It also handles the parts that make naive frame-splitting subtly wrong:
- Skips leading `ID3v2` tags when scanning for the first frame.
- Recognizes and excludes the `Xing`/`Info`/`VBRI` VBR header frame, which is
  encoder metadata, not audio — including it in output or duration
  calculations corrupts both.
- Parses LAME's gapless-playback delay/padding extension, so the reported
  duration matches what a real player shows, not just the raw frame count.

Duration parsing is cross-validated against
[mutagen](https://github.com/quodlibet/mutagen)'s independent implementation
to the millisecond across a range of real-world encoded files (see
[Testing](#testing)).

## Install

Not yet published to PyPI. For now, install directly from GitHub:

```bash
uv add git+https://github.com/jkeychan/waxcut
# or
pip install git+https://github.com/jkeychan/waxcut
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

## Scope

`waxcut` parses **MPEG-1/2/2.5 Audio Layer III** — what "MP3" actually means.
Layer I/II frames are explicitly rejected (`UnsupportedMp3Error`) rather than
silently mishandled, since virtually no real-world "MP3" file uses them.

## Testing

```bash
uv sync
uv run pytest tests/ -v
```

The suite validates frame parsing against mutagen's independent MP3 parser
(duration must match exactly, including LAME gapless delay/padding) across
CBR/VBR, mono/stereo, and multiple encoder tags — including a regression
test for misreading a non-LAME encoder's metadata as if it were LAME's
gapless fields. Where `ffmpeg`/`ffprobe` are available, every split output
is independently decoded to confirm it's a valid, playable file.

## Contributing

Bug reports and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md)
for the dev setup and PR process. Report security vulnerabilities per
[SECURITY.md](SECURITY.md) rather than as public issues.

## License

Apache-2.0 — see [LICENSE](LICENSE).
