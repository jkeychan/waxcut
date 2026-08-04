---
sidebar_position: 3
---

# API Reference

The full public surface of `waxcut` — everything in `waxcut.__all__`. All
names are importable directly from the top-level `waxcut` package.

## `load_audio_stream`

```python
def load_audio_stream(path: Path) -> AudioStream
```

Loads an MP3 file and parses it into an [`AudioStream`](#audiostream) ready
for frame-accurate splitting.

Reads the whole file into memory, scans it for MPEG Layer III frames (see
[`iter_frames`](#iter_frames), which also skips any leading ID3v2 tag), and
checks whether the first frame is a Xing/Info/VBRI VBR header rather than
real audio. If it is, that frame is excluded from the returned `frames`
list, every remaining frame's `start_ms` is rebased so the first real audio
frame starts at 0, and — if the header carries a LAME gapless extension —
`encoder_delay_samples`/`encoder_padding_samples` are extracted from it.

**Args**
- `path` (`Path`) — path to an MP3 file on disk.

**Returns**
- `AudioStream`

**Raises**
- `UnsupportedMp3Error` — no valid MPEG Layer III frame was found anywhere
  in the file (propagated from `iter_frames`), or the file consists of only
  a VBR header frame with no audio frames after it.
- `FileNotFoundError` (and other OS-level errors) — propagated from reading
  the file if `path` doesn't exist or can't be opened.

## `AudioStream`

```python
@dataclass(frozen=True)
class AudioStream:
    data: bytes
    frames: list[Frame]
    encoder_delay_samples: int
    encoder_padding_samples: int
    sample_rate: int
```

A parsed MP3 stream with located frames and gapless metadata. Normally
constructed via [`load_audio_stream`](#load_audio_stream) rather than
directly.

**Fields**
- `data` (`bytes`) — the complete file bytes this stream was parsed from.
- `frames` (`list[Frame]`) — the located frames, in file order. If the
  source file had a Xing/Info/VBRI VBR header frame, it has already been
  excluded here, and the remaining frames rebased so the first one has
  `start_ms == 0`.
- `encoder_delay_samples` (`int`) — samples of encoder padding at the start
  of the audio, read from a LAME gapless tag if one was present; `0`
  otherwise. Informational only: it does not affect frame boundaries or
  where splits can land — real players skip this many samples at the
  start, but split output is fresh audio starting exactly at a frame
  boundary with no delay semantics of its own to carry over.
- `encoder_padding_samples` (`int`) — samples of encoder padding at the end
  of the audio, from the same source; `0` if absent. Same caveat as above
  (real players stop this many samples early; split output has no padding
  semantics of its own).
- `sample_rate` (`int`) — audio sample rate in Hz (e.g. `44100`, `48000`).

**Properties**

- `duration_ms -> float` — total playback duration spanned by `frames`
  (equivalent to `total_duration_ms(self.frames)`).
- `playable_duration_ms -> float` — the duration a real player would
  report: `duration_ms` minus the gapless delay/padding trim (converted
  from samples to milliseconds via `sample_rate`), clamped to a minimum of
  `0.0`.

## `Frame`

```python
@dataclass(frozen=True)
class Frame:
    offset: int
    length: int
    start_ms: float
    duration_ms: float
```

One located MPEG Layer III frame, as produced by
[`iter_frames`](#iter_frames) or found in `AudioStream.frames`.

**Fields**
- `offset` (`int`) — byte offset of this frame's header within the source
  data.
- `length` (`int`) — total length of this frame in bytes (header + side
  info + audio data) — how far to advance from `offset` to reach the next
  frame.
- `start_ms` (`float`) — playback position of this frame's start, in
  milliseconds. Frames returned directly by `iter_frames` are timed from
  the very first frame found in the data; frames on `AudioStream.frames`
  are timed relative to the first *real audio* frame, i.e. after any VBR
  header frame has been excluded by `load_audio_stream`.
- `duration_ms` (`float`) — this frame's own playback duration, in
  milliseconds.

## `frame_index_at`

```python
def frame_index_at(frames: list[Frame], target_ms: float) -> int
```

Returns the index of the last frame in `frames` that starts at or before
`target_ms`. This is how a "split at N milliseconds" request becomes a
frame boundary for [`slice_bytes`](#slice_bytes): a lossless split can only
land on a frame's own start, so this snaps to the nearest one at or before
the requested time.

**Args**
- `frames` (`list[Frame]`) — frame list, e.g. from `iter_frames` or
  `AudioStream.frames`.
- `target_ms` (`float`) — desired split point in milliseconds.

**Returns**
- `int` — an index into `frames`. A `target_ms` before the first frame's
  start clamps to `0`; a `target_ms` at or beyond the last frame's start
  clamps to the last index.

**Current behavior on an empty `frames` list:** `frame_index_at` does not
raise anything itself — the loop body never runs and it returns `0`, which
is not a valid index into an empty list. Callers are responsible for not
passing an empty `frames` list.

## `slice_bytes`

```python
def slice_bytes(data: bytes, frames: list[Frame], start_idx: int, end_idx: int) -> bytes
```

Returns the raw bytes covering `frames[start_idx:end_idx]` as one
contiguous range copied directly out of `data` — this is a byte-copy, not a
re-parse. Frames are assumed contiguous, which holds for any list produced
by `iter_frames` from the same `data`. The result is itself a decodable,
standalone MP3 stream (no container/ID3 wrapper), byte-identical to the
corresponding span of the original file.

**Args**
- `data` (`bytes`) — the same bytes `frames` was derived from.
- `frames` (`list[Frame]`) — frame list from `iter_frames` or
  `AudioStream.frames`.
- `start_idx` (`int`) — first frame index to include (inclusive).
- `end_idx` (`int`) — one past the last frame index to include (exclusive)
  — standard Python slice semantics.

**Returns**
- `bytes` — `b""` if `start_idx >= end_idx`; otherwise the byte span from
  `frames[start_idx].offset` through the end of `frames[end_idx - 1]`.

**Current behavior:** `slice_bytes` does not itself validate that
`start_idx`/`end_idx` are in range for `frames` — passing out-of-range
indices (for example, any non-empty range against an empty `frames` list)
surfaces as a normal Python `IndexError` from indexing `frames[start_idx]`
or `frames[end_idx - 1]`.

## `total_duration_ms`

```python
def total_duration_ms(frames: list[Frame]) -> float
```

Total playback duration spanned by `frames`, in milliseconds — the last
frame's `start_ms` plus its `duration_ms`.

**Args**
- `frames` (`list[Frame]`) — a non-empty list of `Frame`, as returned by
  `iter_frames`.

**Returns**
- `float`

**Raises**
- `IndexError` — `frames` is empty (`frames[-1]` on an empty list).
  `iter_frames` itself never returns an empty list — it raises
  `UnsupportedMp3Error` instead — so this only happens if you pass in an
  empty list you constructed or filtered yourself.

## `iter_frames`

```python
def iter_frames(data: bytes) -> list[Frame]
```

Scans `data` for MPEG Layer III audio frames, skipping any leading ID3v2
tag (via [`id3v2_size`](#id3v2_size)). At each position it tries to parse a
valid frame header; if the header doesn't check out (bad sync word,
unsupported layer, a reserved bitrate/sample-rate index, or a computed
frame length that would run past the end of `data`), the scan advances one
byte and keeps looking — this is what lets it skip past a trailing
ID3v1/APE tag or other non-frame bytes without getting stuck. Every frame
found is recorded with its own `start_ms`/`duration_ms`, timed
cumulatively from the first frame found in `data`.

Note that this returns *every* parsed frame, including a leading
Xing/Info/VBRI VBR header frame if the file has one — excluding that frame
from playback/duration is [`load_audio_stream`](#load_audio_stream)'s job,
not `iter_frames`'s.

**Args**
- `data` (`bytes`) — raw file bytes.

**Returns**
- `list[Frame]` — never empty.

**Raises**
- `UnsupportedMp3Error` — no valid MPEG Layer III frame was found anywhere
  in `data`. This covers both non-MP3 input and files containing only
  Layer I/II frames, which this parser doesn't recognize (see
  [How It Works](./how-it-works.md#why-layer-iii-are-out-of-scope)).

## `id3v2_size`

```python
def id3v2_size(data: bytes) -> int
```

Returns the byte length of a leading ID3v2 tag at the start of `data`, or
`0` if `data` doesn't start with one. The tag's size is read from ID3v2's
syncsafe 4-byte size field and added to the fixed 10-byte header size.

**Args**
- `data` (`bytes`) — raw file bytes.

**Returns**
- `int` — `0` if no ID3v2 tag is present, otherwise the tag's total size
  in bytes, including its 10-byte header.

## `UnsupportedMp3Error`

```python
class UnsupportedMp3Error(ValueError)
```

Raised when frame parsing can't make sense of the input as an MP3. In the
current implementation this covers two cases:

- [`iter_frames`](#iter_frames) finds no valid MPEG Layer III frame
  anywhere in the data — this includes files that aren't MP3s at all, and
  files that contain only Layer I/II frames, which this parser doesn't
  recognize.
- [`load_audio_stream`](#load_audio_stream) finds a file consisting of
  only a VBR header frame (Xing/Info/VBRI) with no real audio frames after
  it.

It subclasses `ValueError`.
