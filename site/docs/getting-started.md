---
sidebar_position: 1
---

# Getting Started

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

## Splitting into more than two parts

`split_at`/`join_frames` collapse the loop above into one call for N cut
points:

```python
from waxcut import load_audio_stream, split_at

stream = load_audio_stream(Path("mixtape.mp3"))
parts = split_at(stream, timestamps_ms=[90_000, 180_000, 270_000])

for i, part in enumerate(parts):
    Path(f"part{i}.mp3").write_bytes(part)
```

## Tagging split output

`write_id3v2_tag` writes a minimal ID3v2.3 tag (title/artist/track number)
onto any `bytes` — typically one segment of `split_at`'s output:

```python
from pathlib import Path
from waxcut import load_audio_stream, split_at, write_id3v2_tag

stream = load_audio_stream(Path("album.mp3"))
segments = split_at(stream, timestamps_ms=[90_000, 180_000, 270_000])

titles = ["Intro", "Second Track", "Third Track", "Outro"]
for i, (segment, title) in enumerate(zip(segments, titles, strict=True), start=1):
    tagged = write_id3v2_tag(segment, title=title, artist="Various Artists", track=i)
    Path(f"track{i}.mp3").write_bytes(tagged)
```

For source files with true variable bitrate (VBR) encoding, some tag readers
and players estimate duration from the first MPEG frame's bitrate rather than
decoding the whole file. Since split output doesn't carry forward the
original Xing/VBRI VBR header, those readers may report an inaccurate
duration for split VBR tracks. This is a property of how splitting works
(frame-accurate byte copying), not a bug in `write_id3v2_tag`.

See [How It Works](./how-it-works.md) for why this approach is safe, and the
[API Reference](./api-reference.md) for the full public surface.
