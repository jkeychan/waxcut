# waxcut

[![PyPI](https://img.shields.io/pypi/v/waxcut.svg)](https://pypi.org/project/waxcut/)
[![CI](https://github.com/jkeychan/waxcut/actions/workflows/ci.yml/badge.svg)](https://github.com/jkeychan/waxcut/actions/workflows/ci.yml)
[![Fuzzing](https://github.com/jkeychan/waxcut/actions/workflows/cflite_pr.yml/badge.svg)](https://github.com/jkeychan/waxcut/actions/workflows/cflite_pr.yml)
[![Docs](https://img.shields.io/badge/docs-waxcut.pages.dev-blue)](https://waxcut.pages.dev/)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/jkeychan/waxcut/badge)](https://scorecard.dev/viewer/?uri=github.com/jkeychan/waxcut)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/13947/badge)](https://www.bestpractices.dev/projects/13947)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/jkeychan/waxcut/blob/main/LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/waxcut.svg)](https://github.com/jkeychan/waxcut/blob/main/pyproject.toml)

Frame-accurate, lossless MP3 splitting and duration parsing in pure Python with
no ffmpeg, no subprocess, no decode step.

Cuts are made by parsing the file's own MPEG frame headers and byte-copying
whole frames: output is byte-identical to the corresponding span of the
source audio frames, just shorter — leading ID3v2 tags, the VBR header
frame, and any trailer present in the source are not carried into split
output.

## Install

```bash
pip install waxcut
# or
uv add waxcut
```

## Usage

Quick duration check from the shell, no script needed — replace `'song.mp3'`
below (keep the quotes) with the path to your own file and run it as-is:

```bash
python -c "from pathlib import Path; from waxcut import load_audio_stream as l; print(round(l(Path('song.mp3')).playable_duration_ms / 1000, 1), 's')"
```

For actually splitting a file, here's the full pattern — load it once, then
cut at whatever timestamp you want:

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

Splitting into more than two parts — `split_at`/`join_frames` collapse the
loop above into one call:

```python
from pathlib import Path
from waxcut import load_audio_stream, split_at, join_frames, slice_bytes

stream = load_audio_stream(Path("mixtape.mp3"))
parts = split_at(stream, timestamps_ms=[90_000, 180_000, 270_000])

for i, part in enumerate(parts):
    Path(f"part{i}.mp3").write_bytes(part)

# join_frames is the inverse: reassembling parts reproduces the source's
# audio frame span (not the original file bytes -- tags/VBR header/trailer
# aren't preserved)
assert join_frames(parts) == slice_bytes(stream.data, stream.frames, 0, len(stream.frames))
```

Tagging split output — `write_id3v2_tag` writes a minimal ID3v2.3 tag
(title/artist/track) onto untagged bytes, typically one segment of
`split_at`'s output:

```python
from pathlib import Path
from waxcut import load_audio_stream, split_at, write_id3v2_tag

stream = load_audio_stream(Path("album.mp3"))
segments = split_at(stream, timestamps_ms=[90_000, 180_000, 270_000])

for i, segment in enumerate(segments, start=1):
    tagged = write_id3v2_tag(segment, title=f"Track {i}", track=i)
    Path(f"track{i}.mp3").write_bytes(tagged)
```

Splitting an album from a `.cue` sheet — `parse_cue_sheet` turns its
`TRACK`/`INDEX 01` entries directly into `split_at`'s `timestamps_ms`, so
you don't have to work out cut points by hand. Malformed cue text raises
`CueSheetError`:

```python
from pathlib import Path
from waxcut import load_audio_stream, parse_cue_sheet, split_at

stream = load_audio_stream(Path("album.mp3"))
timestamps = parse_cue_sheet(Path("album.cue").read_text())
tracks = split_at(stream, timestamps)

for i, track in enumerate(tracks, start=1):
    Path(f"track{i:02d}.mp3").write_bytes(track)
```

Large files — `load_audio_stream(path, use_mmap=True)` memory-maps the
file instead of reading it into a `bytes` object (governed by its own,
larger 2 GB size cap rather than the 250 MB default; both raise
`FileTooLargeError` if exceeded). Pair it with `split_to_files`, which
writes each segment straight to disk instead of collecting them all into
one `list[bytes]` first:

```python
from pathlib import Path
from waxcut import load_audio_stream, split_to_files

with load_audio_stream(Path("huge_mixtape.mp3"), use_mmap=True) as stream:
    cut_points = [90_000, 180_000, 270_000]
    output_paths = [Path(f"part{i}.mp3") for i in range(len(cut_points) + 1)]
    split_to_files(stream, cut_points, output_paths)
```

See the [docs site](https://waxcut.pages.dev/) for the full public
surface — every function/class in `waxcut.__all__`, including the
lower-level pieces the examples above build on (`scan_frames`, `id3v2_size`,
`total_duration_ms`, `AudioStream`, `Frames`) — and
[How It Works](https://waxcut.pages.dev/docs/how-it-works) for why the
approach is safe.

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
to within 1ms (see [Testing](#testing)).

## Scope

Parses **MPEG-1/2/2.5 Audio Layer III** — what "MP3" actually means. Layer
I/II frames raise `UnsupportedMp3Error` rather than being silently
mishandled, since virtually no real-world "MP3" file uses them.

Every exception waxcut raises on purpose — `UnsupportedMp3Error`,
`CueSheetError`, `FileTooLargeError` (a subclass of `UnsupportedMp3Error`)
— is a `WaxcutError`, itself a `ValueError`, so `except WaxcutError` catches
all of them in one place without needing to know about each individually.

## Testing

```bash
uv sync
uv run pytest tests/ -v
```

Validated against mutagen's independent parser (duration must match to
within 1ms, including LAME gapless delay/padding) across CBR/VBR,
mono/stereo, and multiple encoder tags. Where `ffmpeg`/`ffprobe` are
available, every split output is independently decoded to confirm it's
valid. Fuzzed continuously with [ClusterFuzzLite](https://github.com/jkeychan/waxcut/tree/main/.clusterfuzzlite/).

## Contributing

Bug reports and pull requests are welcome — see [CONTRIBUTING.md](https://github.com/jkeychan/waxcut/blob/main/CONTRIBUTING.md)
for the dev setup and PR process. Report security vulnerabilities per
[SECURITY.md](https://github.com/jkeychan/waxcut/blob/main/SECURITY.md) rather than as public issues.

## License

Apache-2.0 — see [LICENSE](https://github.com/jkeychan/waxcut/blob/main/LICENSE).
