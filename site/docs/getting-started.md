---
sidebar_position: 1
---

# Getting Started

Frame-accurate, lossless MP3 splitting and duration parsing in pure Python —
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

`split_at` collapses the loop above into one call for N cut points, and
`join_frames` is its inverse — concatenating the resulting segments
reproduces the original audio's frame span exactly (not the original file
bytes; tags/VBR header/trailer aren't preserved):

```python
from pathlib import Path
from waxcut import load_audio_stream, split_at, join_frames, slice_bytes

stream = load_audio_stream(Path("mixtape.mp3"))
parts = split_at(stream, timestamps_ms=[90_000, 180_000, 270_000])

for i, part in enumerate(parts):
    Path(f"part{i}.mp3").write_bytes(part)

# join_frames is the inverse of split_at: reassembling the parts reproduces
# the source's audio frame span
assert join_frames(parts) == slice_bytes(stream.data, stream.frames, 0, len(stream.frames))
```

## Tagging split output

`write_id3v2_tag` writes a minimal ID3v2.3 tag (title/artist/track number)
onto untagged `bytes` — typically one segment of `split_at`'s output, which
never has a leading ID3v2 tag of its own. It refuses to tag data that
already starts with an ID3v2 tag:

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

## Splitting an album using a .cue sheet

If you already have a `.cue` sheet for the album (the usual companion to a
single-file rip), `parse_cue_sheet` turns its `TRACK`/`INDEX 01` entries
directly into the timestamps `split_at` expects — no need to work out cut
points by hand:

```python
from pathlib import Path
from waxcut import load_audio_stream, parse_cue_sheet, split_at

stream = load_audio_stream(Path("album.mp3"))
cue_text = Path("album.cue").read_text()
timestamps = parse_cue_sheet(cue_text)
tracks = split_at(stream, timestamps)

for i, track in enumerate(tracks, start=1):
    Path(f"track{i:02d}.mp3").write_bytes(track)
```

See [API Reference](./api-reference.md#parse_cue_sheet) for the exact cue
grammar this parses and the errors it raises on malformed input.

## Putting it together: cue sheet, split, and tag

Combining the two sections above, a full cue-sheet-driven rip: parse the cue
sheet, split on its timestamps, and tag each resulting track before writing
it to disk.

Two `INDEX 01` entries can land on the same MPEG frame boundary (cue sheets
are timestamped at 1/75-second CD-frame resolution, finer than an MP3
frame), and a cue sheet can also outrun the actual audio if it doesn't quite
match the file it's paired with. Both produce an empty `split_at` segment,
which `write_id3v2_tag` will happily tag into a file containing nothing but
a tag header — one `waxcut` itself refuses to load back in. Skip empty
segments rather than writing them:

```python
from pathlib import Path
from waxcut import load_audio_stream, parse_cue_sheet, split_at, write_id3v2_tag

stream = load_audio_stream(Path("album.mp3"))
cue_text = Path("album.cue").read_text()
timestamps = parse_cue_sheet(cue_text)
tracks = split_at(stream, timestamps)

titles = ["Intro", "Second Track", "Third Track"]
for i, (segment, title) in enumerate(zip(tracks, titles, strict=True), start=1):
    if len(segment) == 0:
        continue  # sub-frame-resolution or over-long cue entry -- nothing to write
    tagged = write_id3v2_tag(segment, title=title, track=i)
    Path(f"track{i:02d}.mp3").write_bytes(tagged)
```

## Large files: `use_mmap` and `split_to_files`

For a multi-hour file, `load_audio_stream(path, use_mmap=True)` avoids
reading the whole source file into memory (see
[How It Works](./how-it-works.md#loading-large-files-use_mmap)). Pair it
with `split_to_files`, which writes each segment straight to its own output
path instead of returning a `list[bytes]` with every segment held at once:

```python
from pathlib import Path
from waxcut import load_audio_stream, split_to_files

with load_audio_stream(Path("huge_mixtape.mp3"), use_mmap=True) as stream:
    cut_points = [90_000, 180_000, 270_000]  # ms
    output_paths = [Path(f"part{i}.mp3") for i in range(len(cut_points) + 1)]
    split_to_files(stream, cut_points, output_paths)
```

This avoids holding *all* segments in memory at once — each is written and
becomes eligible for garbage collection before the next is cut — but each
individual segment is still fully materialized as one `bytes` object by
`slice_bytes` before it's written, same as `split_at`. It's not a fully
streaming, byte-for-byte pipeline; it just avoids the worst of `split_at`'s
memory profile for this common case. For full control over how each
segment is produced or written, call `frame_index_at`/`slice_bytes`
directly instead of `split_to_files`, the same way `split_to_files` itself
does internally.

See [How It Works](./how-it-works.md) for why this approach is safe, and the
[API Reference](./api-reference.md) for the full public surface.
