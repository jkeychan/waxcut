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

See [How It Works](./how-it-works.md) for why this approach is safe, and the
[API Reference](./api-reference.md) for the full public surface.
