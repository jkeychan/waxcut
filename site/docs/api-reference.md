---
sidebar_position: 3
---

# API Reference

The full public surface of `waxcut` — everything in `waxcut.__all__`. All
names are importable directly from the top-level `waxcut` package.

## `load_audio_stream`

```python
def load_audio_stream(path: Path, *, use_mmap: bool = False) -> AudioStream
```

Loads an MP3 file and parses it into an [`AudioStream`](#audiostream) ready
for frame-accurate splitting.

Reads the whole file into memory, scans it for MPEG Layer III frames (see
[`scan_frames`](#scan_frames), which also skips any leading ID3v2 tag), and
checks whether the first frame is a Xing/Info/VBRI VBR header rather than
real audio. If it is, that frame is excluded from the returned `frames`
list, every remaining frame's `start_ms` is rebased so the first real audio
frame starts at 0, and — if the header carries a LAME gapless extension —
`encoder_delay_samples`/`encoder_padding_samples` are extracted from it.

Pass `use_mmap=True` to memory-map the file instead of reading it into a
`bytes` object — the file's bytes are never fully materialized in memory,
which matters for a multi-hour file. `AudioStream.data` is then an
`mmap.mmap` rather than `bytes` (every function here that accepts `data`
works identically with either), and the file is kept open for the
`AudioStream`'s whole lifetime — call `AudioStream.close()` (or use it as a
context manager) when done with it. `use_mmap=True` is governed by its own,
larger 2 GB size cap rather than the 250 MB default. See
[Security](./security.md#resource-limits) for the rationale behind both
limits.

**Args**
- `path` (`Path`) — path to an MP3 file on disk.
- `use_mmap` (`bool`, keyword-only, default `False`) — if `True`,
  memory-map the file instead of reading it into a `bytes` object; see
  above.

**Returns**
- `AudioStream`

**Raises**
- `UnsupportedMp3Error` — no valid MPEG Layer III frame was found anywhere
  in the file (propagated from `scan_frames`), or the file consists of only
  a VBR header frame with no audio frames after it.
- `FileTooLargeError` — the file exceeds the applicable size limit: 250 MB
  by default, or 2 GB with `use_mmap=True`. Checked against the file's
  size on disk *before* opening it, so an oversized file never gets read
  or mapped in the first place. See [Security](./security.md#resource-limits).
- `FileNotFoundError` (and other OS-level errors) — propagated from reading
  the file if `path` doesn't exist or can't be opened.

## `AudioStream`

```python
@dataclass(frozen=True)
class AudioStream:
    data: bytes | mmap.mmap
    frames: Frames
    encoder_delay_samples: int
    encoder_padding_samples: int
    sample_rate: int
```

A parsed MP3 stream with located frames and gapless metadata. Normally
constructed via [`load_audio_stream`](#load_audio_stream) rather than
directly.

**Fields**
- `data` (`bytes | mmap.mmap`) — the complete file bytes this stream was
  parsed from, or (if loaded with `use_mmap=True`) an `mmap.mmap` view over
  them. Every function in this module that accepts `data` (`scan_frames`,
  `slice_bytes`, etc.) works identically with either.
- `frames` (`Frames`) — the located frames, in file order, as a
  memory-compact sequence (see [`Frames`](#frames)) that behaves like
  `list[Frame]` — indexing, negative indexing, slicing, iteration, `len()`
  all work the same way. If the source file had a Xing/Info/VBRI VBR header
  frame, it has already been excluded here, and the remaining frames
  rebased so the first one has `start_ms == 0`.
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

**Methods**

- `close() -> None` — releases the mmap and file handle backing `data`, if
  any. A no-op when this `AudioStream` was loaded without `use_mmap=True`
  (`data` is a plain, already-materialized `bytes` object with no open file
  handle to release). Safe to call more than once. `AudioStream` also
  supports use as a context manager
  (`with load_audio_stream(path, use_mmap=True) as stream:`), which calls
  `close()` automatically on exit.

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
[`scan_frames`](#scan_frames) or found in `AudioStream.frames`.

**Fields**
- `offset` (`int`) — byte offset of this frame's header within the source
  data.
- `length` (`int`) — total length of this frame in bytes (header + side
  info + audio data) — how far to advance from `offset` to reach the next
  frame.
- `start_ms` (`float`) — playback position of this frame's start, in
  milliseconds. Frames returned directly by `scan_frames` are timed from
  the very first frame found in the data; frames on `AudioStream.frames`
  are timed relative to the first *real audio* frame, i.e. after any VBR
  header frame has been excluded by `load_audio_stream`.
- `duration_ms` (`float`) — this frame's own playback duration, in
  milliseconds.

## `Frames`

```python
class Frames(Sequence[Frame])
```

The type returned by [`scan_frames`](#scan_frames) and found on
`AudioStream.frames`. Behaves like `list[Frame]` — `len()`, positive and
negative indexing, slicing, and iteration all work the same way — but is
backed by four packed `array.array` buffers rather than one Python object
per frame: indexing/iterating constructs a [`Frame`](#frame) on demand
instead of every frame being pre-allocated up front. Measured at ~24
bytes/frame vs. ~168 bytes/frame for an equivalent `list[Frame]` — see
[Security](./security.md#resource-limits) for why this matters and the
real numbers behind it.

Slicing returns another `Frames` sharing the same backing arrays — it
never copies. Not constructed directly by callers.

## `frame_index_at`

```python
def frame_index_at(frames: Sequence[Frame], target_ms: float) -> int
```

Returns the index of the last frame in `frames` that starts at or before
`target_ms`. This is how a "split at N milliseconds" request becomes a
frame boundary for [`slice_bytes`](#slice_bytes): a lossless split can only
land on a frame's own start, so this snaps to the nearest one at or before
the requested time.

**Args**
- `frames` (`Sequence[Frame]`) — e.g. a `Frames` from `scan_frames` or
  `AudioStream.frames`, or a plain `list[Frame]`.
- `target_ms` (`float`) — desired split point in milliseconds.

**Returns**
- `int` — an index into `frames`. A `target_ms` before the first frame's
  start clamps to `0`; a `target_ms` at or beyond the last frame's start
  clamps to the last index.

**On an empty `frames` list, or a NaN `target_ms`:** raises `ValueError`
immediately, rather than returning a meaningless index. (NaN comparisons are
always false, so without this guard a NaN target would silently walk to the
last frame index instead of erroring.)

## `slice_bytes`

```python
def slice_bytes(data: bytes, frames: Sequence[Frame], start_idx: int, end_idx: int) -> bytes
```

Returns the raw bytes covering `frames[start_idx:end_idx]` as one
contiguous range copied directly out of `data` — this is a byte-copy, not a
re-parse. Frames are assumed contiguous, which holds for any list produced
by `scan_frames` from the same `data`. The result is itself a decodable,
standalone MP3 stream (no container/ID3 wrapper), byte-identical to the
corresponding span of the original file.

**Args**
- `data` (`bytes`) — the same bytes `frames` was derived from.
- `frames` (`Sequence[Frame]`) — from `scan_frames` or `AudioStream.frames`.
- `start_idx` (`int`) — first frame index to include (inclusive).
- `end_idx` (`int`) — one past the last frame index to include (exclusive)
  — standard Python slice semantics.

**Returns**
- `bytes` — `b""` if `start_idx >= end_idx`; otherwise the byte span from
  `frames[start_idx].offset` through the end of `frames[end_idx - 1]`.

**On an empty `frames` list:** raises `ValueError`. **On a negative
`start_idx`/`end_idx`:** raises `IndexError` explicitly, rather than
silently wrapping to an unintended frame the way Python's own negative
indexing would. Positive out-of-range indices surface as a normal Python
`IndexError` from indexing `frames[start_idx]` or `frames[end_idx - 1]`.

## `split_at`

```python
def split_at(stream: AudioStream, timestamps_ms: list[float]) -> list[bytes]
```

Convenience wrapper around [`frame_index_at`](#frame_index_at) +
[`slice_bytes`](#slice_bytes) for the common case of cutting at several
timestamps in one call, instead of looping manually.

**Args**
- `stream` (`AudioStream`) — from `load_audio_stream`.
- `timestamps_ms` (`list[float]`) — desired cut points, in milliseconds.
  Need not be sorted or in range — each is clamped by `frame_index_at`, so
  an out-of-order or duplicate timestamp simply produces an empty segment
  at that position rather than raising.

**Returns**
- `list[bytes]` — `len(timestamps_ms) + 1` segments, in order. Each is a
  standalone, decodable MP3 stream. Concatenating all of them (see
  [`join_frames`](#join_frames)) reproduces the original audio exactly.

## `join_frames`

```python
def join_frames(segments: list[bytes]) -> bytes
```

Concatenates frame-aligned MP3 byte segments back into one stream. Safe
because MPEG Layer III frames are self-delimited — each carries its own
length in its header — so concatenation always reproduces the original
bytes exactly, with no re-parsing or re-alignment needed.

**Args**
- `segments` (`list[bytes]`) — byte segments to join, in order, as
  produced by `slice_bytes` or `split_at`.

**Returns**
- `bytes` — the concatenated result.

## `write_id3v2_tag`

```python
def write_id3v2_tag(
    data: bytes,
    *,
    title: str | None = None,
    artist: str | None = None,
    track: int | None = None,
) -> bytes
```

Prepends a fresh, minimal ID3v2.3 tag onto `data`, writing `TIT2` (title),
`TPE1` (artist), and `TRCK` (track number) frames for whichever fields are
given. No padding, no footer, no attempt to detect or merge with a
pre-existing tag already in `data` — this is meant to be called on
[`slice_bytes`](#slice_bytes)/[`split_at`](#split_at) output, which never
has a leading ID3v2 tag of its own.

Text is encoded per-frame: Latin-1 (ID3v2 encoding byte `0x00`) where the
text allows it, UTF-16 with an explicit little-endian BOM (encoding byte
`0x01`) otherwise — UTF-8 is a v2.4-only encoding and would be invalid in
this v2.3 tag.

**Args**
- `data` (`bytes`) — bytes to tag; coerced via `bytes(data)` so a
  `memoryview` or similar is also accepted.
- `title` (`str | None`) — track title, written as a `TIT2` frame if given.
- `artist` (`str | None`) — track artist, written as a `TPE1` frame if given.
- `track` (`int | None`) — track number, written as a `TRCK` frame
  (`str(track)`, no `"N/total"` support yet) if given.

**Returns**
- `bytes` — the ID3v2.3 tag followed immediately by `data`.

**Raises**
- `ValueError` — `track` is given and is less than `1`, or the combined
  frame payload doesn't fit in a 4-byte ID3v2 syncsafe integer.

## `total_duration_ms`

```python
def total_duration_ms(frames: Sequence[Frame]) -> float
```

Total playback duration spanned by `frames`, in milliseconds — the last
frame's `start_ms` plus its `duration_ms`.

**Args**
- `frames` (`Sequence[Frame]`) — non-empty, as returned by `scan_frames`.

**Returns**
- `float`

**Raises**
- `IndexError` — `frames` is empty (`frames[-1]` on an empty sequence).
  `scan_frames` itself never returns an empty `Frames` — it raises
  `UnsupportedMp3Error` instead — so this only happens if you pass in an
  empty sequence you constructed or filtered yourself.

## `parse_cue_sheet`

```python
def parse_cue_sheet(text: str) -> list[float]
```

Parses CUE-sheet text into cut-point timestamps, in milliseconds.

Extracts each AUDIO TRACK's INDEX 01 timestamp from a single-FILE CUE sheet
(the standard shape for a ripped album: one audio file, several TRACK/INDEX
01 entries marking where each track starts). The first track's INDEX 01 is
almost always `00:00:00` and is dropped from the output — it isn't a real
cut point, the stream already starts there; feeding a leading `0.0` into
`split_at` would otherwise produce a spurious empty first segment. Any other
collected timestamp, including a genuinely nonzero first one, is kept.

Recognized but ignored: `REM` comments, `TITLE`/`PERFORMER`/`SONGWRITER`
(disc- and track-level), `CATALOG`, `CDTEXTFILE`, `ISRC`, `FLAGS`,
`PREGAP`/`POSTGAP`, `INDEX 00` and `INDEX 02`+, and any `TRACK` whose type
isn't `AUDIO` (its `INDEX` lines are skipped, not treated as errors).

`MM:SS:FF` is CD Red Book timecode — minutes, seconds, and CD "frames" (0-74
at 75 frames/second) — unrelated to and not to be confused with an MPEG
audio [`Frame`](#frame) elsewhere on this page; it's a fixed 1/75-second CD
unit, not a frame of audio data.

**Args**
- `text` (`str`) — the full contents of a `.cue` file, already decoded to
  `str`.

**Returns**
- `list[float]` — cut-point timestamps in milliseconds, in the order tracks
  appear, ready to pass directly as [`split_at`](#split_at)'s
  `timestamps_ms` argument. Empty if the cue sheet describes only a single
  track.

**Raises**
- `CueSheetError` — `text` contains no AUDIO TRACK with an INDEX 01 entry;
  contains more than one FILE line (multi-FILE cue sheets, where each
  TRACK's audio lives in a different file, aren't supported — their
  timestamps aren't comparable without knowing per-file boundaries); an
  INDEX 01 timestamp isn't valid MM:SS:FF (wrong field count, non-numeric
  fields, seconds outside 0-59, or the CD frame field outside 0-74 at 75
  frames/second); an AUDIO track's block ends without ever recording its own
  INDEX 01; or a later INDEX 01 timestamp is strictly less than the one
  before it (equal, i.e. duplicate, timestamps are allowed — `split_at`
  already documents that a duplicate timestamp simply yields an empty
  segment).

**Example**

Given this single-`FILE`, 3-track cue sheet:

```
REM GENRE Reggae
REM DATE 1978
PERFORMER "The Wailers"
TITLE "Kaya"
FILE "kaya.mp3" MP3
  TRACK 01 AUDIO
    TITLE "Easy Skanking"
    PERFORMER "The Wailers"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "Kaya"
    PERFORMER "The Wailers"
    INDEX 00 03:22:18
    INDEX 01 03:25:37
  TRACK 03 AUDIO
    TITLE "Sun Is Shining"
    PERFORMER "The Wailers"
    INDEX 01 07:00:00
```

`parse_cue_sheet(text)` returns:

```python
[205493.33333333334, 420000.0]
```

Track 1's `INDEX 01 00:00:00` is dropped (the stream already starts there),
track 2's `INDEX 00` (pregap) is ignored, and track 2's and track 3's
`INDEX 01` entries become the two cut points — feeding this list directly
into `split_at(stream, timestamps)` produces exactly 3 segments, one per
track.

## `scan_frames`

```python
def scan_frames(data: bytes, *, max_size: int | None = None) -> Frames
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
not `scan_frames`'s.

**Args**
- `data` (`bytes`) — raw file bytes.
- `max_size` (`int | None`, keyword-only, default `None`) — maximum
  allowed size in bytes; `None` means the default 250 MB cap.
  [`load_audio_stream`](#load_audio_stream) uses this internally to apply
  its own larger 2 GB cap when called with `use_mmap=True`. Most callers
  should leave this at the default.

**Returns**
- [`Frames`](#frames) — never empty.

**Raises**
- `UnsupportedMp3Error` — no valid MPEG Layer III frame was found anywhere
  in `data`. This covers both non-MP3 input and files containing only
  Layer I/II frames, which this parser doesn't recognize (see
  [How It Works](./how-it-works.md#why-layer-iii-are-out-of-scope)).
- `FileTooLargeError` — `data` exceeds `max_size` (250 MB by default). See
  [Security](./security.md#resource-limits).

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

- [`scan_frames`](#scan_frames) finds no valid MPEG Layer III frame
  anywhere in the data — this includes files that aren't MP3s at all, and
  files that contain only Layer I/II frames, which this parser doesn't
  recognize.
- [`load_audio_stream`](#load_audio_stream) finds a file consisting of
  only a VBR header frame (Xing/Info/VBRI) with no real audio frames after
  it.

It subclasses `ValueError`.

## `CueSheetError`

```python
class CueSheetError(ValueError)
```

Raised when cue-sheet text can't be parsed into cut-point timestamps.
Covers malformed MM:SS:FF timestamps, a TRACK with no INDEX 01, cue text
with no audio tracks at all, out-of-order INDEX 01 timestamps, and
multi-FILE cue sheets (unsupported — see
[`parse_cue_sheet`](#parse_cue_sheet)). Always raised with a message naming
the offending line.

It subclasses `ValueError`.

## `FileTooLargeError`

```python
class FileTooLargeError(UnsupportedMp3Error)
```

Raised by [`scan_frames`](#scan_frames)/[`load_audio_stream`](#load_audio_stream)
when input exceeds 250 MB — see [Security](./security.md#resource-limits)
for why this limit exists. Subclasses `UnsupportedMp3Error` (and therefore
`ValueError`), so an existing `except UnsupportedMp3Error` handler still
catches it. It's a distinct class so callers who want to tell "too large"
apart from "not a valid MP3" can catch it specifically.
