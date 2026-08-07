---
sidebar_position: 2
---

# How It Works

waxcut splits MP3 files without decoding or re-encoding any audio, and
without shelling out to ffmpeg or any other external binary. This page
explains why that's possible and how each piece works, so you can trust the
output.

## MPEG frames are self-describing

An MP3 file is a sequence of independent MPEG Audio Layer III frames, each
with its own 4-byte header. That header starts with an 11-bit sync word
(`0xFFE`), followed by fields for the MPEG version, layer, bitrate index,
sample rate index, and padding bit. Critically, those fields are enough to
compute the frame's exact length in bytes on their own — no need to decode
the audio data that follows.

waxcut's `scan_frames` jumps straight to each candidate sync byte with
`bytes.find(b"\xff", ...)` (a fast C-level scan, not a per-byte Python loop),
decodes the header fields at each candidate (`waxcut.frames._parse_header`),
computes the frame length from them, and records a `Frame(offset, length,
start_ms, duration_ms)`. If the header decodes to a length that doesn't fit
in the remaining data, or the sync word doesn't check out, the scanner
advances one byte and keeps looking — this is what lets it skip over
non-frame bytes such as a trailing ID3v1/APE tag without getting confused. A
buffer that never forms a valid sync at all forces a failed header-parse
attempt at every byte; a bound on consecutive failed attempts
(`_MAX_CONSECUTIVE_RESYNC_FAILURES`) keeps that adversarial case fast too —
see [Security](./security.md#resource-limits).

Because every frame's boundaries are derived directly from its own header,
frame-accurate splitting is a byte-copy operation: `slice_bytes` doesn't need
to touch or understand the audio payload at all, it just copies the byte
range spanning `frames[start_idx].offset` through the end of
`frames[end_idx - 1]`. The result is a valid, self-contained MP3 stream that
is byte-identical to the corresponding span of the source file.

## Leading ID3v2 tags

Files commonly start with an ID3v2 tag (artwork, metadata) before the first
audio frame. `id3v2_size` reads the tag's syncsafe size field and returns how
many bytes to skip, so frame scanning starts at the right offset instead of
tripping over tag bytes that happen to look frame-like.

## Xing/Info/VBRI exclusion

Many encoders write a special first "frame" that isn't audio at all — a
`Xing`, `Info`, or `VBRI` header containing encoder metadata (total frame
count, byte count, sometimes a seek table). It has a valid MPEG frame header
so a naive scanner would treat it like any other frame, but including it in
playback or duration calculations is wrong: it isn't sound, and its
duration doesn't represent playback time.

`load_audio_stream` locates this tag by checking, immediately after the side
info of the *first* parsed frame, for one of the three recognized 4-byte
markers (`_vbr_header_tag_offset`). The side info size itself depends on the
MPEG version and channel mode (mono vs. stereo/joint-stereo), since that
changes where the tag would start. If a VBR header frame is found, it's
dropped from the returned `AudioStream.frames`, and every remaining frame's
`start_ms` is rebased so the first real audio frame starts at 0. If a file
turns out to contain *only* a VBR header frame with no audio after it,
`load_audio_stream` raises `UnsupportedMp3Error` rather than returning an
empty, useless stream.

## LAME gapless delay/padding

Real MP3 encoders don't start writing audio at sample 0: their filterbank
needs a fixed number of samples of lookahead before it can produce real
output, so every encode has a few hundred samples of silence prepended (and,
depending on how the input's length lines up with the frame grid, a few
appended at the end too). Players that want gapless playback need to skip
that lookahead padding, and LAME encoders record exactly how much in an
extension appended after the standard Xing/Info tag fields
(`_parse_lame_gapless`).

waxcut reads that extension defensively: it only trusts the delay/padding
values if the 9 bytes at the expected offset literally start with the ASCII
string `LAME` — the signature genuine LAME encodes write into that field.
Other encoders (for example ffmpeg's native `Lavc` encoder) produce a
Xing/Info header in the same position without this extension, so bytes read
at that offset from a non-LAME file would be unrelated data. Even after
confirming the `LAME` signature, the decoded delay and padding values are
checked against the 12-bit range the field's own bit width allows (`0` to
`4095`) — every value that bit-unpacking can actually produce already
satisfies this, so it isn't a check a legitimately-encoded file could ever
fail. It exists as a defensive guard against a bug elsewhere in the
bit-unpacking producing an out-of-range value, not as a filter that rejects
real-world input. If any of these checks fail, waxcut falls back to
`encoder_delay_samples = 0` and `encoder_padding_samples = 0`.

These values are informational: `AudioStream.playable_duration_ms` uses them
to report the duration a real player would show (trimmed from the raw
frame-derived `duration_ms`), matching what tools like
[mutagen](https://github.com/quodlibet/mutagen) compute independently. They
don't change where splits can land — frame boundaries, and therefore valid
cut points, are unaffected by gapless metadata, and split output carries no
delay/padding semantics of its own since it's fresh audio starting exactly
at a frame boundary.

## Loading large files: `use_mmap`

By default, `load_audio_stream` reads the whole file into a `bytes` object
before scanning it — simple, and fast for anything up to a normal song or
album length. For a multi-hour file, holding the whole thing in RAM just to
locate frame boundaries is wasteful, so `load_audio_stream(path,
use_mmap=True)` memory-maps the file instead: the OS pages bytes in on
demand rather than waxcut materializing all of them in the Python heap up
front. `scan_frames`/`slice_bytes`/every other function that takes `data`
works identically against an `mmap.mmap` or a `bytes` object, so this is a
drop-in switch, not a different API.

The tradeoff is lifetime, not correctness: the file is kept open for as long
as the `AudioStream` is alive, so callers must call `AudioStream.close()` (or
use it as a context manager) when done — the default `bytes` path has no
such requirement, since the file handle closes as soon as the read
completes. `use_mmap=True` is governed by its own, larger 2 GB size cap
(vs. 250 MB by default), and is exercised in CI on Linux only — see
[Security](./security.md#resource-limits) for both.

Note that `use_mmap` addresses *loading*, not the whole splitting pipeline:
`split_at` still returns every segment as a fully materialized `list[bytes]`,
so a full parse-split-write run still peaks at roughly the size of all
segments held at once, regardless of how the source file was loaded. For a
very large file, `split_to_files` writes each segment straight to its own
output path instead of collecting them all first, so segments already
written become eligible for garbage collection before the next one is cut —
but each individual segment is still fully materialized as one `bytes`
object by `slice_bytes` before it's written, same as `split_at`. It avoids
holding *all* segments in memory simultaneously; it isn't a fully streaming
byte-for-byte pipeline.

## Why Layer I/II are out of scope

"MP3" colloquially means MPEG Audio Layer III, but the MPEG Audio standard
also defines Layer I and Layer II, which use different frame layouts,
bitrate tables, and samples-per-frame counts. Virtually no real-world file
extension `.mp3` actually contains Layer I or II audio. Rather than
partially support them with tables and logic that isn't validated the same
way, waxcut's header parser only recognizes Layer III (`_LAYER_III` in
`_parse_header`) — any other layer value is treated the same as an invalid
sync, and a file containing no Layer III frames raises
`UnsupportedMp3Error`. This is a deliberate scope boundary: rejecting clearly
and loudly is safer than silently mis-parsing bytes as the wrong layer.

## Validation

Because none of this involves an actual decoder, correctness is cross-checked
against tools that do decode: duration output is compared
against [mutagen](https://github.com/quodlibet/mutagen)'s independent
parser across CBR/VBR encodes, mono/stereo, and multiple encoder tags, and
where `ffmpeg`/`ffprobe` are available, every split output is independently
decoded to confirm it's a valid, playable MP3. The parser is also fuzzed
continuously — see [Security](./security.md) for details.
