"""Pure-Python MPEG Audio Layer III (MP3) frame parsing.

No decoding, no subprocess, no external binary: frames are located by
scanning for valid sync/header bytes and their length is computed from the
header fields, so splitting can byte-copy whole frames without touching
audio data. This is the same approach frame-accurate tools like mp3splt use
to cut MP3s losslessly.

Reference: ISO/IEC 11172-3, the frame header layout at
http://www.mp3-tech.org/programmer/frame_header.html
"""

from __future__ import annotations

import bisect
import enum
import math
import mmap
import struct
from array import array
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from io import BufferedReader
from itertools import pairwise
from pathlib import Path
from typing import overload


class MpegVersion(enum.Enum):
    """MPEG audio version. Not a continuous quantity -- 2.5 is a de facto
    extension to the standard (low sample rates), not "halfway between 2
    and 3". A plain Literal[1, 2, 2.5] isn't legal under PEP 586 (no float
    in a Literal), and mixing int/float dict keys risks a silent hash
    collision (hash(2) == hash(2.0)) -- an Enum sidesteps both.
    """

    MPEG1 = 1
    MPEG2 = 2
    MPEG2_5 = 2.5


FRAME_SYNC_MASK = 0xFFE00000
FRAME_SYNC = 0xFFE00000

# index -> MPEG version; 0b01 is reserved and never appears in valid frames
_VERSIONS: dict[int, MpegVersion] = {
    0b00: MpegVersion.MPEG2_5,
    0b10: MpegVersion.MPEG2,
    0b11: MpegVersion.MPEG1,
}
# index -> layer number; 0b00 is reserved. "MP3" is specifically Layer III —
# Layer I/II frames are rejected rather than mishandled (see _parse_header).
_LAYER_III = 0b01

# version -> bitrate index -> kbps, Layer III only; index 0 is "free" (unsupported), 15 is invalid
_BITRATES_KBPS: dict[MpegVersion, list[int | None]] = {
    MpegVersion.MPEG1: [None, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, None],
    MpegVersion.MPEG2: [None, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, None],
}
_BITRATES_KBPS[MpegVersion.MPEG2_5] = _BITRATES_KBPS[MpegVersion.MPEG2]

# version -> sample rate index -> Hz; index 3 is reserved
_SAMPLE_RATES: dict[MpegVersion, list[int | None]] = {
    MpegVersion.MPEG1: [44100, 48000, 32000, None],
    MpegVersion.MPEG2: [22050, 24000, 16000, None],
    MpegVersion.MPEG2_5: [11025, 12000, 8000, None],
}

# version -> samples per Layer III frame
_SAMPLES_PER_FRAME: dict[MpegVersion, int] = {
    MpegVersion.MPEG1: 1152,
    MpegVersion.MPEG2: 576,
    MpegVersion.MPEG2_5: 576,
}

# (version, mono) -> side info size in bytes, i.e. where a Xing/Info/VBRI
# tag (if present) starts relative to the frame's own offset.
_SIDE_INFO_SIZE: dict[tuple[MpegVersion, bool], int] = {
    (MpegVersion.MPEG1, False): 32,
    (MpegVersion.MPEG1, True): 17,
    (MpegVersion.MPEG2, False): 17,
    (MpegVersion.MPEG2, True): 9,
    (MpegVersion.MPEG2_5, False): 17,
    (MpegVersion.MPEG2_5, True): 9,
}
_VBR_HEADER_TAGS = (b"Xing", b"Info", b"VBRI")

# The Fraunhofer VBRI header sits at a fixed offset of 32 bytes past the
# 4-byte frame header, independent of channel mode, side-info size, or CRC --
# unlike Xing/Info, which immediately follow the side info.
_VBRI_FIXED_OFFSET = 36


class WaxcutError(ValueError):
    """Common base for every exception this package raises on purpose.

    Lets a caller catch every waxcut-specific error with one `except
    WaxcutError` rather than needing to know about each individual
    exception tree (UnsupportedMp3Error's, CueSheetError's, ...)
    separately. Still a ValueError subclass, so existing `except
    ValueError` handlers written before this base class existed keep
    working unchanged.
    """


class UnsupportedMp3Error(WaxcutError):
    """Raised when frame parsing can't make sense of the input.

    This covers both "not an MP3 at all" (no valid Layer III sync found)
    and the one genuine edge case in load_audio_stream: a file consisting
    of only a Xing/Info/VBRI header frame with no real audio after it.
    """


# A crafted file packed with minimum-size frames (as little as ~24 bytes
# each for MPEG2/2.5 Layer III) parses in linear time. Frames' compact
# array-backed storage keeps memory amplification near 1x even for such a
# file (measured; see SECURITY.md) -- it was ~6x before that redesign
# (Frame objects boxed per-frame). This limit now exists to bound the
# absolute worst-case time/memory of a single call, not to guard against
# amplification specifically: 250 MB comfortably covers legitimate use
# (even multi-hour, high-bitrate recordings) while keeping a single call's
# cost bounded. See SECURITY.md for the full threat-model note.
_MAX_FILE_SIZE_BYTES = 250 * 1024 * 1024  # 250 MiB

# use_mmap=True doesn't load the whole file into a Python bytes object, so
# the memory-amplification rationale behind _MAX_FILE_SIZE_BYTES doesn't
# apply here -- but scan_frames' worst-case adversarial cost is still O(n)
# in wall-clock time regardless of what backs `data`. ~90 MB/s (measured
# against a 20.6MB file of minimum-size MPEG2.5 frames -- see SECURITY.md
# and bench/security_claims.py; hardware-dependent, re-measure rather than
# treating this as a portable constant) is the valid-frame-carpet case,
# where _parse_header only runs once per frame; it is not the true worst
# case. The true worst case is a buffer that never forms a valid sync at
# all (e.g. a carpet of 0xFF bytes), forcing a per-byte _parse_header
# attempt -- measured at ~6 MB/s (1-8MB all-0xFF buffers). At that rate,
# scanning the full 2 GiB cap byte-by-byte would take ~5.5 minutes -- but
# scan_frames never actually gets there on adversarial input:
# _MAX_CONSECUTIVE_RESYNC_FAILURES (below) aborts the scan after a bounded
# run of consecutive failed resync attempts, long before byte count alone
# would force the issue. This 2 GiB cap is safe against the adversarial
# case for that reason, not because the raw scan is fast enough to finish
# -- it isn't. It still matters for the valid-frame-carpet case (every
# failed attempt there is followed by a real frame, so the resync-count
# bound never trips), where ~90 MB/s keeps a full 2 GiB scan to well under
# a minute.
#
# Must stay under 4 GiB: frame offsets are stored in an array("I") (4-byte
# unsigned int, max ~4.29 GB) in scan_frames below -- raising this cap past
# that would silently overflow there.
_MAX_MMAP_FILE_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

# Bounds scan_frames' worst-case wall-clock cost independent of either size
# cap above: an adversarial buffer that never forms a valid sync (e.g. a
# carpet of 0xFF bytes) forces a per-byte _parse_header attempt, measured
# at ~6 MB/s (~160ns/byte -- see _MAX_MMAP_FILE_SIZE_BYTES above). Without
# this bound, that reaches ~5.5 minutes before the 2 GiB use_mmap cap forces
# the issue. 2,000,000 consecutive failed attempts, at that same ~160ns
# each, is ~360ms (measured; see bench/security_claims.py and the A9
# follow-up fix commit) -- tight enough to keep worst-case cost sub-second
# regardless of which size cap applies, while generous enough that no
# legitimate file trips it: a real file's non-frame bytes are its ID3v2
# tag, already skipped by id3v2_size before this loop starts, and
# 2,000,000 consecutive candidate-but-invalid 0xFF bytes with no real frame
# in between anywhere else is far beyond what any genuinely
# corrupted-but-real MP3 (a few damaged frames, a stray ID3v1/APE trailer)
# would ever produce.
_MAX_CONSECUTIVE_RESYNC_FAILURES = 2_000_000

_MIB_PER_GIB = 1024  # used to format FileTooLargeError's message below


class FileTooLargeError(UnsupportedMp3Error):
    """Raised when input exceeds the applicable size limit.

    The default limit is _MAX_FILE_SIZE_BYTES (250 MB); load_audio_stream
    applies the larger _MAX_MMAP_FILE_SIZE_BYTES (2 GB) instead when called
    with use_mmap=True.

    A subclass of UnsupportedMp3Error, so existing `except
    UnsupportedMp3Error` handlers still catch it -- but it's a distinct
    class for callers who want to tell "too large" apart from "not a valid
    MP3" (e.g. to show a different error message).
    """


@dataclass(frozen=True, slots=True)
class Frame:
    """One located MPEG Layer III frame.

    Attributes:
        offset: Byte offset of this frame's header within the source data.
        length: Total frame length in bytes (header + side info + audio data).
        start_ms: Playback position of this frame's start. For frames from
            scan_frames, start_ms is 0-based from the first frame found in
            the byte stream (VBR header frame included if present). For
            frames from AudioStream.frames (via load_audio_stream), the VBR
            header frame is excluded and start_ms is rebased so the first
            real audio frame has start_ms=0.
        duration_ms: This frame's own playback duration.
    """

    offset: int
    length: int
    start_ms: float
    duration_ms: float


class Frames(Sequence["Frame"]):
    """A memory-compact, lazily-materialized sequence of Frame.

    Backed by four parallel array.array buffers (packed, unboxed values)
    instead of a list of Frame objects -- ~24 bytes/frame vs. ~128
    bytes/frame for an equivalent list[Frame] (measured; see SECURITY.md),
    since no per-frame Python object or boxed int/float is allocated until
    you actually index into it. Supports the same operations a list[Frame]
    does: len(), positive and negative indexing, slicing (returns another
    Frames, sharing the same backing arrays -- slicing never copies), and
    iteration. Stepped slicing (e.g. frames[::2]) is not supported and
    raises TypeError -- real step support for this array-backed view is
    nontrivial and not needed by any caller in this codebase. Unlike
    list[Frame], equality is identity-based: this class defines no
    __eq__, so two Frames views over equal underlying data are only ==
    if they're the same object.

    Not constructed directly by callers -- returned by scan_frames and
    found on AudioStream.frames.
    """

    __slots__ = ("_duration_ms", "_lengths", "_offsets", "_start", "_start_ms", "_start_ms_bias", "_stop")

    def __init__(
        self, offsets: array[int], lengths: array[int], start_ms: array[float], duration_ms: array[float]
    ) -> None:
        self._offsets = offsets
        self._lengths = lengths
        self._start_ms = start_ms
        self._duration_ms = duration_ms
        self._start = 0
        self._stop = len(offsets)
        self._start_ms_bias = 0.0

    @classmethod
    def _view(cls, base: Frames, start: int, stop: int, start_ms_bias: float) -> Frames:
        """Construct a Frames sharing base's backing arrays, bypassing __init__."""
        view = object.__new__(cls)
        view._offsets = base._offsets
        view._lengths = base._lengths
        view._start_ms = base._start_ms
        view._duration_ms = base._duration_ms
        view._start = start
        # A reversed slice (frames[5:2]) would otherwise give a negative
        # __len__, which CPython rejects with ValueError -- clamp to an
        # empty view, matching what list does for the same slice.
        view._stop = max(start, stop)
        view._start_ms_bias = start_ms_bias
        return view

    def __len__(self) -> int:
        return self._stop - self._start

    def _frame_at(self, real_index: int) -> Frame:
        return Frame(
            offset=self._offsets[real_index],
            length=self._lengths[real_index],
            start_ms=self._start_ms[real_index] - self._start_ms_bias,
            duration_ms=self._duration_ms[real_index],
        )

    @overload
    def __getitem__(self, index: int) -> Frame: ...
    @overload
    def __getitem__(self, index: slice) -> Frames: ...

    def __getitem__(self, index: int | slice) -> Frame | Frames:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step != 1:
                raise TypeError("Frames slicing does not support a step")
            return Frames._view(self, self._start + start, self._start + stop, self._start_ms_bias)
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError("Frames index out of range")
        return self._frame_at(self._start + index)

    def __iter__(self) -> Iterator[Frame]:
        for i in range(self._start, self._stop):
            yield self._frame_at(i)

    def rebase(self, offset_ms: float) -> Frames:
        """A view with every start_ms reduced by offset_ms, without copying."""
        return Frames._view(self, self._start, self._stop, self._start_ms_bias + offset_ms)


_ID3V2_HEADER_SIZE = 10


def id3v2_size(data: bytes | mmap.mmap) -> int:
    """Return the byte length of a leading ID3v2 tag, or 0 if there isn't one.

    Args:
        data: Raw file bytes. Any bytes-like object indexable/sliceable
            the same way as bytes is accepted.

    Returns:
        The size in bytes of the ID3v2 tag if present (including the 10-byte
        header), or 0 if no ID3v2 tag is found.
    """
    if len(data) < _ID3V2_HEADER_SIZE or data[:3] != b"ID3":
        return 0
    # Tag size is a 4-byte syncsafe integer (7 significant bits per byte).
    size = 0
    for byte in data[6:10]:
        size = (size << 7) | (byte & 0x7F)
    # Flags bit 0x10 (ID3v2.4 only) means a 10-byte footer -- a mirror of
    # the header -- immediately follows the tag body, and its length isn't
    # included in the syncsafe size field above.
    if data[5] & 0x10:
        size += 10
    return _ID3V2_HEADER_SIZE + size


_ID3V2_TAG_VERSION = b"\x03\x00"  # ID3v2.3.0 -- see plan doc for why not 2.4
_LATIN1_ENCODING_BYTE = b"\x00"
_UTF16_ENCODING_BYTE = b"\x01"
_UTF16_LE_BOM = b"\xff\xfe"


def _syncsafe(n: int) -> bytes:
    """Encode `n` as a 4-byte ID3v2 syncsafe integer (7 significant bits/byte)."""
    if not 0 <= n < (1 << 28):
        raise ValueError(f"{n} does not fit in a 4-byte ID3v2 syncsafe integer (max {(1 << 28) - 1})")
    return bytes(((n >> shift) & 0x7F) for shift in (21, 14, 7, 0))


_FORBIDDEN_TEXT_CHARS = ("\x00", "\r", "\n")


def _encode_text(text: str) -> bytes:
    """ID3v2.3 text-frame content: 1-byte encoding flag + encoded text.

    Latin-1 (encoding byte 0x00) when the text is representable in it --
    the common case, and the most compact. UTF-16 with an explicit
    little-endian BOM (encoding byte 0x01) otherwise. UTF-8 (encoding byte
    0x03) is a v2.4-only addition and is invalid in a v2.3 tag, which is
    why this doesn't just always use UTF-8.

    Raises:
        ValueError: `text` contains NUL, CR, or LF -- these pass through
            the encoding step unremarked, but a NUL truncates the field
            for any reader that treats it as a C string terminator, and
            CR/LF can make what's stored differ from what's displayed
            (e.g. multi-line-looking output from a single-line field).
            Rejecting them is safer than silently stripping, which could
            surprise a caller with different content than what they
            passed in.
    """
    for char in _FORBIDDEN_TEXT_CHARS:
        if char in text:
            raise ValueError(f"write_id3v2_tag() text fields cannot contain {char!r}, got {text!r}")
    try:
        return _LATIN1_ENCODING_BYTE + text.encode("latin-1")
    except UnicodeEncodeError:
        return _UTF16_ENCODING_BYTE + _UTF16_LE_BOM + text.encode("utf-16-le")


def _text_frame(frame_id: bytes, text: str) -> bytes:
    """One complete ID3v2.3 text frame: 10-byte frame header + encoded content."""
    content = _encode_text(text)
    # v2.3 frame sizes are plain big-endian (NOT syncsafe -- that's v2.4 only).
    size = struct.pack(">I", len(content))
    flags = b"\x00\x00"
    return frame_id + size + flags + content


def write_id3v2_tag(
    data: bytes,
    *,
    title: str | None = None,
    artist: str | None = None,
    track: int | None = None,
) -> bytes:
    """Prepend a fresh, minimal ID3v2.3 tag onto `data`.

    Writes TIT2 (title), TPE1 (artist), and TRCK (track number) frames --
    whichever of the three fields are given -- with no padding and no
    footer. Intended for `data` that has no leading ID3v2 tag of its own,
    which is always true of slice_bytes/split_at output (see their
    docstrings): this function detects a pre-existing leading ID3v2 tag
    and refuses to tag over it (see Raises below) rather than stacking a
    second tag on top of it.

    Known limitation: this function does not implement ID3v2 unsynchronisation
    (the spec-defined scheme of inserting a 0x00 byte after every 0xFF byte
    in the tag body, so a false MPEG sync pattern can never occur inside a
    tag). A crafted title/artist could in principle contain bytes that,
    combined with adjacent frame bytes, form a false MPEG sync word (e.g.
    0xFF 0xFB) inside the tag body -- waxcut itself is unaffected (it always
    skips the tag via id3v2_size before scanning for frames), but a naive or
    non-compliant player that doesn't honor unsynchronisation, or that scans
    for sync words without first parsing the ID3v2 header, could misdecode
    tag bytes as audio before the real content. See SECURITY.md.

    Args:
        data: Bytes to tag, typically the output of slice_bytes or one
            element of split_at's return value. Coerced to bytes via
            bytes(data) before concatenation, so a memoryview or other
            bytes-like object is also accepted.
        title: Track title (TIT2), or None to omit that frame.
        artist: Track artist (TPE1), or None to omit that frame.
        track: Track number (TRCK), written as str(track) with no
            "N/total" support in this version, or None to omit that
            frame.

    Returns:
        A new bytes object: the ID3v2.3 tag followed immediately by
        `data`, unmodified.

    Raises:
        ValueError: `track` is given and is less than 1, `title`/`artist`
            contains NUL, CR, or LF (see _encode_text), `data` already
            starts with an ID3v2 tag (stacking a second tag on top would
            corrupt frame scanning, since scan_frames/id3v2_size only ever
            skip the outermost tag), or the combined size of the requested
            frames does not fit in a 4-byte ID3v2 syncsafe integer (the
            ~256 MB tag-size ceiling the format itself imposes --
            effectively unreachable for title/artist/track text, but
            guarded rather than silently overflowing).
    """
    data = bytes(data)
    if track is not None and track < 1:
        raise ValueError(f"write_id3v2_tag() requires track >= 1, got {track}")
    existing_tag_size = id3v2_size(data)
    if existing_tag_size:
        raise ValueError(
            f"write_id3v2_tag() refuses to tag data that already has a leading ID3v2 tag "
            f"({existing_tag_size} bytes) -- this would corrupt frame scanning. Pass untagged "
            f"split output only."
        )

    frame_bytes = b""
    if title is not None:
        frame_bytes += _text_frame(b"TIT2", title)
    if artist is not None:
        frame_bytes += _text_frame(b"TPE1", artist)
    if track is not None:
        frame_bytes += _text_frame(b"TRCK", str(track))

    header = b"ID3" + _ID3V2_TAG_VERSION + b"\x00" + _syncsafe(len(frame_bytes))
    return header + frame_bytes + data


def _parse_header(data: bytes | mmap.mmap, offset: int) -> tuple[MpegVersion, int, int, int] | None:
    """Return (version, bitrate_kbps, sample_rate, padding) for a valid Layer III header, else None."""
    if offset + 4 > len(data):
        return None
    header = struct.unpack_from(">I", data, offset)[0]
    if header & FRAME_SYNC_MASK != FRAME_SYNC:
        return None

    version = _VERSIONS.get((header >> 19) & 0b11)
    if version is None or (header >> 17) & 0b11 != _LAYER_III:
        return None

    bitrate_idx = (header >> 12) & 0b1111
    sample_rate_idx = (header >> 10) & 0b11
    padding = (header >> 9) & 0b1

    bitrate = _BITRATES_KBPS[version][bitrate_idx]
    sample_rate = _SAMPLE_RATES[version][sample_rate_idx]
    if bitrate is None or sample_rate is None:
        return None

    return version, bitrate, sample_rate, padding


def _frame_length(version: MpegVersion, bitrate_kbps: int, sample_rate: int, padding: int) -> int:
    coefficient = 144 if version is MpegVersion.MPEG1 else 72
    return int(coefficient * bitrate_kbps * 1000 / sample_rate) + padding


_CHANNEL_MODE_MONO = 0b11
_CRC_SIZE = 2  # bytes, present between the header and side info when protection_bit is 0


def _is_mono(header: int) -> bool:
    channel_mode = (header >> 6) & 0b11
    return channel_mode == _CHANNEL_MODE_MONO


def _has_crc(header: int) -> bool:
    protection_bit = (header >> 16) & 0b1
    return protection_bit == 0


def _vbr_header_tag_offset(data: bytes | mmap.mmap, frame: Frame, version: MpegVersion) -> int | None:
    """Byte offset (relative to `frame.offset`) of a Xing/Info/VBRI tag, if this frame is one."""
    header = struct.unpack_from(">I", data, frame.offset)[0]
    side_info = _SIDE_INFO_SIZE[(version, _is_mono(header))]
    crc = _CRC_SIZE if _has_crc(header) else 0
    xing_start = 4 + crc + side_info
    # A Xing/Info/VBRI tag lives inside its own frame, so bound the probe by
    # the frame's end as well as the buffer's: bytes past frame.offset +
    # frame.length belong to whatever follows (another frame, a trailer, or
    # nothing at all near EOF) and must not be read as this frame's tag.
    tag_limit = min(len(data), frame.offset + frame.length)

    if frame.offset + xing_start + 4 <= tag_limit:
        tag = data[frame.offset + xing_start : frame.offset + xing_start + 4]
        if tag in (b"Xing", b"Info"):
            return xing_start

    if frame.offset + _VBRI_FIXED_OFFSET + 4 <= tag_limit:
        tag = data[frame.offset + _VBRI_FIXED_OFFSET : frame.offset + _VBRI_FIXED_OFFSET + 4]
        if tag == b"VBRI":
            return _VBRI_FIXED_OFFSET

    return None


_LAME_DELAY_PADDING_SIZE = 3  # bytes holding two packed 12-bit fields
_TWELVE_BIT_FIELD_LIMIT = 4096


def _parse_lame_gapless(data: bytes | mmap.mmap, frame: Frame, tag_offset: int) -> tuple[int, int] | None:
    """Extract (encoder_delay_samples, encoder_padding_samples) from a LAME extension tag, if present."""
    pos = frame.offset + tag_offset
    # The LAME extension lives inside the same frame as the Xing/Info tag
    # that precedes it, so bound reads by the frame's end as well as the
    # buffer's — same reasoning as _vbr_header_tag_offset. Without this,
    # bytes past frame.offset + frame.length belong to whatever follows
    # (another frame, a trailer, or nothing at all near EOF) and would be
    # misread as this frame's gapless data instead of just failing to parse.
    tag_limit = min(len(data), frame.offset + frame.length)
    # The tag's own 4 bytes plus the 4-byte flags word that follows them.
    # _vbr_header_tag_offset only guarantees the tag itself is present, so
    # this must be checked here or the unpack below raises a raw
    # struct.error out of the public API on a truncated/crafted file.
    if pos + 8 > tag_limit:
        return None
    flags = struct.unpack_from(">I", data, pos + 4)[0]
    pos += 8
    for flag_bit, size in ((0b0001, 4), (0b0010, 4), (0b0100, 100), (0b1000, 4)):
        if flags & flag_bit:
            pos += size

    lame_start = pos
    if lame_start + 24 > tag_limit:
        return None
    version_string = data[lame_start : lame_start + 9]
    # Only genuine LAME encodes reliably populate the extended gapless
    # delay/padding fields in this layout — other encoders (e.g. ffmpeg's
    # native "Lavc..." tag) use the same Xing/Info header but not this
    # extension, so reading these bytes for them would be misinterpreting
    # unrelated data that happens to pass a numeric range check by luck.
    if not version_string.startswith(b"LAME"):
        return None

    delay_padding = data[lame_start + 21 : lame_start + 24]
    if len(delay_padding) != _LAME_DELAY_PADDING_SIZE:
        return None
    delay = (delay_padding[0] << 4) | (delay_padding[1] >> 4)
    padding = ((delay_padding[1] & 0x0F) << 8) | delay_padding[2]

    if not (0 <= delay < _TWELVE_BIT_FIELD_LIMIT and 0 <= padding < _TWELVE_BIT_FIELD_LIMIT):
        return None
    return delay, padding


def scan_frames(data: bytes | mmap.mmap, *, max_size: int | None = None) -> Frames:
    """Scan raw MP3 bytes and return every located frame, in order.

    Skips a leading ID3v2 tag if present. Includes the Xing/Info/VBRI
    VBR header frame if the stream has one — callers that need audio-only
    frames (with encoder metadata excluded and start_ms rebased to 0)
    should use load_audio_stream instead, which calls this internally.

    Args:
        data: Raw file bytes. bytes, bytearray, or mmap.mmap -- anything
            supporting .find() the same way bytes does -- is accepted; a
            memoryview is not (it has no .find()), despite superficially
            looking bytes-like otherwise. A plain bytes object is the
            tested and expected case.
        max_size: Maximum allowed size in bytes. Defaults to 250 MB, the
            same cap load_audio_stream applies by default; pass a larger
            value to override it (load_audio_stream does this internally
            to apply its own, larger 2 GB cap when called with
            use_mmap=True). Most callers should leave this at the default.

    Returns:
        A Frames sequence in file order, each with byte offset/length and
        cumulative start_ms/duration_ms. Supports len(), positive/negative
        indexing, iteration, and slicing with a step of 1 (a stepped slice
        raises TypeError) the same way list[Frame] does, but is backed by
        compact packed arrays rather than one Python object per frame, and
        compares by identity rather than value (see the Frames class
        docstring for both caveats in full).

    Raises:
        UnsupportedMp3Error: No valid MPEG-1/2/2.5 Layer III frame was
            found anywhere in `data`. This is the correct outcome for
            non-MP3 files, empty input, and Layer I/II files (rejected
            on purpose — see module docstring). Also raised if
            _MAX_CONSECUTIVE_RESYNC_FAILURES consecutive candidate sync
            bytes each fail header validation without a real frame in
            between -- input that looks nothing like an MP3 is rejected
            quickly instead of scanning all the way to max_size (see
            _MAX_CONSECUTIVE_RESYNC_FAILURES's own comment).
        FileTooLargeError: `data` exceeds max_size. See SECURITY.md.
    """
    if max_size is None:
        max_size = _MAX_FILE_SIZE_BYTES
    if len(data) > max_size:
        raise FileTooLargeError(f"Input is {len(data)} bytes, exceeding the {max_size}-byte limit.")
    offset = id3v2_size(data)
    offsets: array[int] = array("I")
    lengths: array[int] = array("I")
    start_ms_values: array[float] = array("d")
    duration_ms_values: array[float] = array("d")
    cursor_ms = 0.0
    consecutive_resync_failures = 0

    def _resync_failed() -> None:
        # Called on every failed resync attempt (a candidate 0xFF byte
        # that didn't turn into a real frame). Unbounded consecutive
        # failures is exactly the adversarial cost _MAX_CONSECUTIVE_RESYNC_FAILURES
        # guards against -- see its own comment above.
        nonlocal consecutive_resync_failures
        consecutive_resync_failures += 1
        if consecutive_resync_failures > _MAX_CONSECUTIVE_RESYNC_FAILURES:
            raise UnsupportedMp3Error(
                "No valid MPEG audio frame found in the first "
                f"{_MAX_CONSECUTIVE_RESYNC_FAILURES} consecutive resync attempts; "
                "giving up rather than scanning the rest of the input."
            )

    while True:
        # Every valid sync requires the byte at `offset` to be exactly 0xFF
        # (the top byte of FRAME_SYNC_MASK) -- _parse_header would reject
        # any other byte immediately anyway, so jump straight past them with
        # a fast C-level scan instead of calling into _parse_header (struct
        # unpack + bit masking) for every single byte of a non-frame gap
        # (ID3v1/APE trailers, padding, adversarial filler).
        offset = data.find(b"\xff", offset)
        if offset == -1:
            break

        parsed = _parse_header(data, offset)
        if parsed is None:
            # 0xFF matched but the rest of the header didn't -- a coincidental
            # byte, not a real sync. Advance one byte and keep scanning.
            offset += 1
            _resync_failed()
            continue

        version, bitrate, sample_rate, padding = parsed
        length = _frame_length(version, bitrate, sample_rate, padding)
        if length <= 0 or offset + length > len(data):
            offset += 1
            _resync_failed()
            continue
        consecutive_resync_failures = 0

        samples = _SAMPLES_PER_FRAME[version]
        duration_ms = samples / sample_rate * 1000

        # start_ms is deliberately an accumulated running total, not
        # `len(offsets) * duration_ms`: this function must tolerate malformed
        # or adversarial input (see the fuzz harness), where a byte sequence
        # a few frames in could spuriously resync at a different apparent
        # version/sample_rate. Accumulating each frame's own duration keeps
        # start_ms correct per-frame even then; assuming a single constant
        # duration for the whole file would not. Storing start_ms/duration_ms
        # per-frame (rather than deriving them from index * a constant) is
        # for the same reason.
        offsets.append(offset)
        lengths.append(length)
        start_ms_values.append(cursor_ms)
        duration_ms_values.append(duration_ms)
        cursor_ms += duration_ms
        offset += length

    if not offsets:
        raise UnsupportedMp3Error("No valid MPEG audio frames found.")
    return Frames(offsets, lengths, start_ms_values, duration_ms_values)


def total_duration_ms(frames: Sequence[Frame]) -> float:
    """Total playback duration spanned by `frames`, in milliseconds.

    Args:
        frames: A non-empty Sequence[Frame], as returned by scan_frames.

    Returns:
        The last frame's start_ms + duration_ms.

    Raises:
        IndexError: `frames` is empty. scan_frames never returns an empty
            list (it raises UnsupportedMp3Error instead), so this only
            happens if you've filtered or otherwise constructed an empty
            list yourself.
    """
    last = frames[-1]
    return last.start_ms + last.duration_ms


def frame_index_at(frames: Sequence[Frame], target_ms: float) -> int:
    """Index of the last frame starting at or before `target_ms`.

    This is how you turn a "cut at N milliseconds" request into a frame
    boundary for slice_bytes — frame-accurate splitting can only land on
    a frame's own start, so this snaps to the nearest one at or before
    the requested time.

    Args:
        frames: A non-empty sequence of Frame, as returned by scan_frames or
            found on AudioStream.frames.
        target_ms: Desired split point in milliseconds. Values below the
            first frame's start_ms clamp to index 0; values at or beyond
            the last frame's start clamp to the last frame's index.

    Returns:
        An index into `frames`, always in range `[0, len(frames) - 1]`
        for a non-empty input.

    Raises:
        ValueError: `frames` is empty, or `target_ms` is NaN.
    """
    if not frames:
        raise ValueError("frame_index_at() requires a non-empty frame list")
    if math.isnan(target_ms):
        raise ValueError("frame_index_at() requires a non-NaN target_ms")
    # Binary search rather than a linear scan: start_ms is always strictly
    # increasing (duration_ms -- samples / sample_rate -- is always
    # positive), so this touches O(log n) frames instead of O(n). That
    # matters more than it used to: indexing into a Frames constructs a
    # Frame on demand, so a linear scan over a large file would construct
    # one per frame just to check start_ms.
    idx = bisect.bisect_right(frames, target_ms, key=lambda frame: frame.start_ms) - 1
    return max(idx, 0)


def slice_bytes(data: bytes | mmap.mmap, frames: Sequence[Frame], start_idx: int, end_idx: int) -> bytes:
    """Byte range covering frames[start_idx:end_idx], contiguous.

    This is a plain byte-copy, not a re-parse: frames are assumed
    contiguous (true for anything scan_frames produced from the same
    `data`), so the range is just [frames[start_idx].offset,
    frames[end_idx - 1] end).

    Args:
        data: The same bytes `frames` was derived from.
        frames: A Sequence[Frame] from scan_frames or AudioStream.frames.
        start_idx: First frame index to include (inclusive).
        end_idx: One past the last frame index to include (exclusive) —
            standard Python slice semantics.

    Returns:
        The raw bytes for that frame range. Empty bytes if
        `start_idx >= end_idx` -- including when both are equally far out
        of range for `frames` (e.g. start_idx=len(frames)+1,
        end_idx=len(frames)+1): this check runs before either index is
        used to index into `frames`, so an empty range never raises even
        if its indices wouldn't be valid on their own. This output is
        itself a decodable MP3 stream (no container/ID3 wrapper),
        byte-identical to the corresponding span of the original file.

    Raises:
        ValueError: `frames` is empty.
        IndexError: `start_idx` or `end_idx` is negative, or a non-empty
            range (`start_idx < end_idx`) reaches an index out of range
            for `frames`.
    """
    if not frames:
        raise ValueError("slice_bytes() requires a non-empty frame list")
    if start_idx < 0 or end_idx < 0:
        raise IndexError(
            f"slice_bytes() requires non-negative indices, got start_idx={start_idx}, end_idx={end_idx}"
        )
    if start_idx >= end_idx:
        return b""
    start = frames[start_idx].offset
    end = frames[end_idx - 1].offset + frames[end_idx - 1].length
    return data[start:end]


@dataclass(frozen=True, slots=True, eq=False)
class AudioStream:
    """Parsed MP3 stream with located frames and gapless metadata.

    Normally constructed via load_audio_stream, not directly. Supports use
    as a context manager (`with load_audio_stream(...) as stream:`) or
    explicit stream.close() -- required when loaded with use_mmap=True to
    release the underlying file handle and mmap; a harmless no-op
    otherwise. Equality/hashing are identity-based (`object`'s default --
    two AudioStreams parsed from the same file are not `==`), regardless
    of use_mmap: eq=False opts out of the field-wise __eq__/__hash__ a
    frozen dataclass generates by default, which would otherwise compare
    (and hash) the full `data` field -- reading the entire file on every
    equality check or hash() call, and still reporting two independently-
    parsed streams as equal since `data` is the only field capable of
    comparing equal by value in the first place.

    Attributes:
        data: The complete file bytes this AudioStream was parsed from, or
            (if loaded with use_mmap=True) an mmap.mmap view over them.
            Every function in this module that accepts `data` (scan_frames,
            slice_bytes, etc.) works identically with either.
        frames: Located MPEG Layer III frames, in file order, excluding any
            VBR header frame. start_ms of the first frame is 0.
        encoder_delay_samples: Gapless playback trim from a LAME encoder tag,
            in samples at the stream's own sample rate — informational only.
            Frame boundaries (and therefore where splits can land) are
            unaffected: real players skip this many samples at the start,
            but split output is fresh audio starting exactly at a frame
            boundary with no delay semantics of its own to carry over.
        encoder_padding_samples: Gapless playback trim from a LAME encoder
            tag, in samples at the stream's own sample rate — informational
            only. Real players stop this many samples early, but split
            output is fresh audio with no padding semantics to carry over.
        sample_rate: Audio sample rate in Hz (e.g. 44100, 48000), read
            from the first frame's own header only. A stream with a
            genuinely mixed sample rate across frames (rare, but legal
            per the MPEG spec) isn't specially detected or handled --
            this always reflects the first frame, and later frames at a
            different rate are parsed and split normally but don't
            change what this attribute reports.
    """

    data: bytes | mmap.mmap
    frames: Frames
    encoder_delay_samples: int
    encoder_padding_samples: int
    sample_rate: int
    _file: BufferedReader | None = field(default=None, repr=False, compare=False)

    @property
    def duration_ms(self) -> float:
        return total_duration_ms(self.frames)

    @property
    def playable_duration_ms(self) -> float:
        """Duration a player would report, after trimming LAME gapless delay/padding."""
        trim_ms = (self.encoder_delay_samples + self.encoder_padding_samples) / self.sample_rate * 1000
        return max(0.0, self.duration_ms - trim_ms)

    def close(self) -> None:
        """Release the mmap and file handle backing `data`, if any.

        A no-op when this AudioStream was loaded without `use_mmap=True`
        (`data` is a plain, already-materialized `bytes` object with no
        open file handle to release). Safe to call more than once.
        """
        if isinstance(self.data, mmap.mmap) and not self.data.closed:
            self.data.close()
        if self._file is not None and not self._file.closed:
            self._file.close()

    def __enter__(self) -> AudioStream:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def load_audio_stream(path: Path | str, *, use_mmap: bool = False) -> AudioStream:
    """Load an MP3 file and parse all its frames for frame-accurate splitting.

    Opens and reads the file, scans it for MPEG Layer III frames (skipping
    any leading ID3v2 tag), and extracts gapless playback metadata if a
    LAME encoder tag is present in the VBR header frame. The VBR header
    frame itself (if found) is excluded from the returned frames list, so
    AudioStream.frames contains only real audio data and start_ms of the
    first frame is 0.

    By default (use_mmap=False), the entire file is read into memory as a
    bytes object before scanning starts -- simple and fast for the common
    case (songs, short clips), but for a multi-hour file this means holding
    the whole thing in RAM just to locate frame boundaries.

    Args:
        path: Path to an MP3 file on disk. A str is accepted too, coerced
            to a Path immediately via Path(path).
        use_mmap: If True, memory-map the file instead of reading it into a
            bytes object. AudioStream.data is then an mmap.mmap rather than
            bytes -- scan_frames/slice_bytes/etc. work identically either
            way (mmap.mmap supports the same len()/slicing/indexing
            operations), and slice_bytes's return value is always plain
            bytes regardless of this flag (a slice of an mmap.mmap is a
            fresh bytes copy, same as slicing bytes). The file is kept open
            for the AudioStream's whole lifetime: call AudioStream.close()
            when done, or use the AudioStream as a context manager
            (`with load_audio_stream(path, use_mmap=True) as stream:`).
            Governed by a separate, larger size cap than the default path
            (2 GB vs. 250 MB) -- see FileTooLargeError below. Not modifying
            or deleting the file on disk while an AudioStream from this
            mode is open: on POSIX, deleting it is safe (the mapping keeps
            working via the file's inode), but modifying it in place is not
            -- the bytes scan_frames/slice_bytes see could change mid-read.
            On Windows, deleting or renaming an open-mapped file typically
            fails outright instead (untested in this repo's CI, which is
            Linux-only -- see SECURITY.md).

    Returns:
        An AudioStream containing the file's bytes (or an mmap view of them
        if use_mmap=True), parsed frames, extracted gapless metadata, and
        sample rate. The returned AudioStream.frames is ready for use with
        frame_index_at and slice_bytes for frame-accurate splitting.

    Raises:
        UnsupportedMp3Error: No valid MPEG Layer III frame was found in the
            file, or the file consists only of a VBR header frame with no
            real audio data after it, or (use_mmap=True only) the file is
            empty -- matching the same error the default path raises for
            an empty file, rather than a platform-specific mmap ValueError.
        FileTooLargeError: The file exceeds the applicable size limit --
            250 MB by default, or 2 GB with use_mmap=True. Checked against
            the file's size on disk before opening it, so an oversized file
            is never read or mapped in the first place. See SECURITY.md.
        FileNotFoundError: The file at `path` does not exist.
    """
    path = Path(path)
    file_size = path.stat().st_size
    max_size = _MAX_MMAP_FILE_SIZE_BYTES if use_mmap else _MAX_FILE_SIZE_BYTES
    if file_size > max_size:
        limit_mb = max_size / (1024 * 1024)
        limit_str = f"{limit_mb / _MIB_PER_GIB:.0f} GB" if limit_mb >= _MIB_PER_GIB else f"{limit_mb:.0f} MB"
        raise FileTooLargeError(
            f"{path} is {file_size} bytes, exceeding the {max_size}-byte ({limit_str}) limit."
        )

    data: bytes | mmap.mmap
    file_handle = None
    if use_mmap:
        if file_size == 0:
            # mmap.mmap() raises ValueError on a zero-byte file; match the
            # bytes path's behavior (empty data -> UnsupportedMp3Error from
            # scan_frames) instead of leaking that platform-specific error.
            raise UnsupportedMp3Error("No valid MPEG audio frames found.")
        # Not opened via `with`: this handle is kept open for AudioStream's
        # lifetime and released via AudioStream.close().
        file_handle = path.open("rb")
        mmap_created = False
        try:
            data = mmap.mmap(file_handle.fileno(), 0, access=mmap.ACCESS_READ)
            mmap_created = True
        finally:
            # try/finally rather than except Exception: a KeyboardInterrupt
            # during mmap.mmap() isn't an Exception subclass, so an except
            # Exception clause wouldn't run this cleanup and the fd would
            # leak. finally runs on every exit path except success, where
            # mmap_created is already True and this is a no-op.
            if not mmap_created:
                file_handle.close()
    else:
        data = path.read_bytes()

    try:
        raw_frames = scan_frames(data, max_size=max_size)
        first = raw_frames[0]
        parsed = _parse_header(data, first.offset)
        if parsed is None:
            # scan_frames only ever records an offset it already validated
            # as a real frame header, so re-parsing that same offset can't
            # actually fail -- this narrows the type for mypy and documents
            # the invariant, rather than a real runtime possibility. A bare
            # `assert` is avoided here since this project's ruff config
            # (bandit S101) flags asserts in non-test code -- they're
            # stripped under `python -O`, unlike an explicit raise.
            raise AssertionError("scan_frames already validated this offset as a real frame header")
        version, _, sample_rate, _ = parsed
        tag_offset = _vbr_header_tag_offset(data, first, version)

        if tag_offset is None:
            return AudioStream(data, raw_frames, 0, 0, sample_rate, _file=file_handle)

        gapless = _parse_lame_gapless(data, first, tag_offset)
        delay, padding = gapless if gapless else (0, 0)

        # The Xing/Info/VBRI frame is encoder metadata, not audio: drop it
        # and rebase remaining frames so start_ms=0 is the first real audio
        # frame. .rebase() is O(1) -- a view over the same backing arrays
        # with an adjusted start_ms bias, not a rebuild of every remaining
        # Frame.
        rebased = raw_frames[1:].rebase(first.duration_ms)
        if not rebased:
            raise UnsupportedMp3Error("File contains only a VBR header frame, no audio.")
        return AudioStream(data, rebased, delay, padding, sample_rate, _file=file_handle)
    except Exception:
        if use_mmap:
            data.close()  # type: ignore[union-attr]
            file_handle.close()  # type: ignore[union-attr]
        raise


def split_at(stream: AudioStream, timestamps_ms: Sequence[float]) -> list[bytes]:
    """Split `stream` into segments at the given cut points.

    Convenience wrapper around frame_index_at + slice_bytes for the common
    case of cutting at N timestamps in one call.

    Args:
        stream: An AudioStream from load_audio_stream.
        timestamps_ms: Desired cut points, in milliseconds. Need not be
            sorted or in range — each is passed through frame_index_at,
            which clamps out-of-range values, and the resulting frame
            indices are then sorted, so unsorted input is normalized to
            ascending cut points rather than producing overlapping (and
            therefore audio-duplicating) segments. A duplicate timestamp,
            or two timestamps landing on the same frame, still yields an
            empty segment between them.

    Returns:
        A list of len(timestamps_ms) + 1 byte segments, in ascending time
        order. The count only ever depends on how many timestamps were
        passed — sorting reorders where the cuts land, never how many
        segments come back — but the segments are ordered by position in
        the stream, not by the order the timestamps were given in. Each
        segment is a standalone, decodable MP3 stream, byte-identical to
        the corresponding span of stream.data. Concatenating all segments
        (see join_frames) reproduces the original audio exactly, for any
        input order — safe because MPEG Layer III frames are
        self-delimited (each carries its own length in its header), so
        rejoining segment boundaries never needs re-parsing or
        re-alignment. Not the original file bytes, though: leading ID3v2
        tags, the VBR header frame, and any trailer aren't carried into
        split output, so they're absent from a rejoin too. Each segment
        is fresh, independent bytes — safe to use even after closing an
        mmap-backed stream (see AudioStream.close()).
    """
    # Sorted so the pairwise ranges below are non-overlapping: unsorted
    # indices would pair into ranges that revisit the same frames, and
    # join_frames on those segments would duplicate audio rather than
    # reproduce the original. frame_index_at still runs against each
    # timestamp as given -- only the resulting indices are sorted.
    idxs = sorted([0, *(frame_index_at(stream.frames, t) for t in timestamps_ms), len(stream.frames)])
    return [slice_bytes(stream.data, stream.frames, start, end) for start, end in pairwise(idxs)]


def split_to_files(stream: AudioStream, timestamps_ms: Sequence[float], output_paths: Sequence[Path]) -> None:
    """Split `stream` into segments at the given cut points, writing each to disk.

    Same cut-point semantics as split_at (see its docstring for the full
    explanation of sorting/clamping/duplicate-timestamp behavior), but
    writes each segment straight to its own output path via
    Path.write_bytes instead of returning them all as one list[bytes].
    For a stream loaded with use_mmap=True, this avoids split_at's
    failure mode of holding every segment (and therefore the whole file)
    in the Python heap at once -- each segment is written and then
    eligible for garbage collection before the next one is sliced. Note
    this is about not accumulating *all* segments at once: each
    individual segment is still fully materialized as one bytes object
    by slice_bytes before being written, same as split_at.

    Args:
        stream: An AudioStream from load_audio_stream.
        timestamps_ms: Desired cut points, in milliseconds. See split_at's
            docstring for sorting/clamping/duplicate-timestamp semantics
            -- identical here.
        output_paths: One path per output segment, in ascending stream
            order (not the order timestamps_ms was given in -- same
            reordering split_at itself applies). Must have exactly
            len(timestamps_ms) + 1 entries, one per segment split_at
            would have returned. Existing files at these paths are
            overwritten.

    Raises:
        ValueError: len(output_paths) != len(timestamps_ms) + 1.
    """
    idxs = sorted([0, *(frame_index_at(stream.frames, t) for t in timestamps_ms), len(stream.frames)])
    segment_count = len(idxs) - 1
    if len(output_paths) != segment_count:
        raise ValueError(
            f"split_to_files() requires exactly {segment_count} output_paths "
            f"for {len(timestamps_ms)} timestamps_ms, got {len(output_paths)}"
        )
    for (start, end), path in zip(pairwise(idxs), output_paths, strict=True):
        path.write_bytes(slice_bytes(stream.data, stream.frames, start, end))


def join_frames(segments: Sequence[bytes]) -> bytes:
    """Concatenate frame-aligned MP3 byte segments back into one stream.

    b"".join(segments) plus the name: see split_at's docstring for why
    this is always safe for segments produced by slice_bytes/split_at.

    Args:
        segments: Byte segments to join, in order, as produced by
            slice_bytes or split_at.

    Returns:
        The concatenated bytes.
    """
    return b"".join(segments)
