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

import struct
from dataclasses import dataclass
from pathlib import Path

FRAME_SYNC_MASK = 0xFFE00000
FRAME_SYNC = 0xFFE00000

# index -> MPEG version; 0b01 is reserved and never appears in valid frames
_VERSIONS = {0b00: 2.5, 0b10: 2, 0b11: 1}
# index -> layer number; 0b00 is reserved. "MP3" is specifically Layer III —
# Layer I/II frames are rejected rather than mishandled (see _parse_header).
_LAYER_III = 0b01

# version -> bitrate index -> kbps, Layer III only; index 0 is "free" (unsupported), 15 is invalid
_BITRATES_KBPS: dict[float, list[int | None]] = {
    1: [None, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, None],
    2: [None, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, None],
}
_BITRATES_KBPS[2.5] = _BITRATES_KBPS[2]

# version -> sample rate index -> Hz; index 3 is reserved
_SAMPLE_RATES: dict[float, list[int | None]] = {
    1: [44100, 48000, 32000, None],
    2: [22050, 24000, 16000, None],
    2.5: [11025, 12000, 8000, None],
}

# version -> samples per Layer III frame
_SAMPLES_PER_FRAME = {1: 1152, 2: 576, 2.5: 576}

# (version, mono) -> side info size in bytes, i.e. where a Xing/Info/VBRI
# tag (if present) starts relative to the frame's own offset.
_SIDE_INFO_SIZE = {
    (1, False): 32,
    (1, True): 17,
    (2, False): 17,
    (2, True): 9,
    (2.5, False): 17,
    (2.5, True): 9,
}
_VBR_HEADER_TAGS = (b"Xing", b"Info", b"VBRI")


class UnsupportedMp3Error(ValueError):
    """Raised when a frame header uses a variant this parser doesn't handle."""


@dataclass(frozen=True)
class Frame:
    offset: int
    length: int
    start_ms: float
    duration_ms: float


def id3v2_size(data: bytes) -> int:
    """Return the byte length of a leading ID3v2 tag, or 0 if there isn't one."""
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    # Tag size is a 4-byte syncsafe integer (7 significant bits per byte).
    size = 0
    for byte in data[6:10]:
        size = (size << 7) | (byte & 0x7F)
    return 10 + size


def _parse_header(data: bytes, offset: int) -> tuple[float, int, int, int] | None:
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


def _frame_length(version: float, bitrate_kbps: int, sample_rate: int, padding: int) -> int:
    coefficient = 144 if version == 1 else 72
    return int(coefficient * bitrate_kbps * 1000 / sample_rate) + padding


def _is_mono(data: bytes, offset: int) -> bool:
    header = struct.unpack_from(">I", data, offset)[0]
    channel_mode = (header >> 6) & 0b11
    return channel_mode == 0b11


def _vbr_header_tag_offset(data: bytes, frame: Frame, version: float) -> int | None:
    """Byte offset (relative to `frame.offset`) of a Xing/Info/VBRI tag, if this frame is one."""
    side_info = _SIDE_INFO_SIZE[(version, _is_mono(data, frame.offset))]
    tag_start = 4 + side_info
    if frame.offset + tag_start + 4 > len(data):
        return None
    tag = data[frame.offset + tag_start : frame.offset + tag_start + 4]
    return tag_start if tag in _VBR_HEADER_TAGS else None


def _parse_lame_gapless(data: bytes, frame: Frame, tag_offset: int) -> tuple[int, int] | None:
    """Extract (encoder_delay_samples, encoder_padding_samples) from a LAME extension tag, if present."""
    pos = frame.offset + tag_offset
    flags = struct.unpack_from(">I", data, pos + 4)[0]
    pos += 8
    for flag_bit, size in ((0b0001, 4), (0b0010, 4), (0b0100, 100), (0b1000, 4)):
        if flags & flag_bit:
            pos += size

    lame_start = pos
    if lame_start + 24 > len(data):
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
    if len(delay_padding) != 3:
        return None
    delay = (delay_padding[0] << 4) | (delay_padding[1] >> 4)
    padding = ((delay_padding[1] & 0x0F) << 8) | delay_padding[2]

    if not (0 <= delay < 4096 and 0 <= padding < 4096):
        return None
    return delay, padding


def iter_frames(data: bytes) -> list[Frame]:
    """Scan `data` for MPEG audio frames, skipping any leading ID3v2 tag."""
    offset = id3v2_size(data)
    frames: list[Frame] = []
    cursor_ms = 0.0

    while offset < len(data):
        parsed = _parse_header(data, offset)
        if parsed is None:
            # Not a frame boundary (could be trailing ID3v1/APE tag, or padding).
            # Advance one byte and keep scanning for the next valid sync.
            offset += 1
            continue

        version, bitrate, sample_rate, padding = parsed
        length = _frame_length(version, bitrate, sample_rate, padding)
        if length <= 0 or offset + length > len(data):
            offset += 1
            continue

        samples = _SAMPLES_PER_FRAME[version]
        duration_ms = samples / sample_rate * 1000

        frames.append(Frame(offset=offset, length=length, start_ms=cursor_ms, duration_ms=duration_ms))
        cursor_ms += duration_ms
        offset += length

    if not frames:
        raise UnsupportedMp3Error("No valid MPEG audio frames found.")
    return frames


def total_duration_ms(frames: list[Frame]) -> float:
    last = frames[-1]
    return last.start_ms + last.duration_ms


def frame_index_at(frames: list[Frame], target_ms: float) -> int:
    """Index of the last frame starting at or before `target_ms` (clamped to range)."""
    idx = 0
    for i, frame in enumerate(frames):
        if frame.start_ms > target_ms:
            break
        idx = i
    return idx


def slice_bytes(data: bytes, frames: list[Frame], start_idx: int, end_idx: int) -> bytes:
    """Byte range covering frames[start_idx:end_idx], contiguous so no re-parsing needed."""
    if start_idx >= end_idx:
        return b""
    start = frames[start_idx].offset
    end = frames[end_idx - 1].offset + frames[end_idx - 1].length
    return data[start:end]


@dataclass(frozen=True)
class AudioStream:
    data: bytes
    frames: list[Frame]
    # Gapless playback trim from a LAME encoder tag, in samples at the
    # stream's own sample rate — informational only. Frame boundaries (and
    # therefore where splits can land) are unaffected: real players skip
    # `encoder_delay` samples at the start and stop `encoder_padding` samples
    # early, but split output is fresh audio starting exactly at a frame
    # boundary, with no delay/padding semantics of its own to carry over.
    encoder_delay_samples: int
    encoder_padding_samples: int
    sample_rate: int

    @property
    def duration_ms(self) -> float:
        return total_duration_ms(self.frames)

    @property
    def playable_duration_ms(self) -> float:
        """Duration a player would report, after trimming LAME gapless delay/padding."""
        trim_ms = (self.encoder_delay_samples + self.encoder_padding_samples) / self.sample_rate * 1000
        return max(0.0, self.duration_ms - trim_ms)


def load_audio_stream(path: Path) -> AudioStream:
    data = path.read_bytes()
    raw_frames = iter_frames(data)

    first = raw_frames[0]
    version, _, sample_rate, _ = _parse_header(data, first.offset)
    tag_offset = _vbr_header_tag_offset(data, first, version)

    if tag_offset is None:
        return AudioStream(data, raw_frames, 0, 0, sample_rate)

    gapless = _parse_lame_gapless(data, first, tag_offset)
    delay, padding = gapless if gapless else (0, 0)

    # The Xing/Info/VBRI frame is encoder metadata, not audio: drop it and
    # rebase remaining frames so start_ms=0 is the first real audio frame.
    rebased = [
        Frame(
            offset=f.offset,
            length=f.length,
            start_ms=f.start_ms - first.duration_ms,
            duration_ms=f.duration_ms,
        )
        for f in raw_frames[1:]
    ]
    if not rebased:
        raise UnsupportedMp3Error("File contains only a VBR header frame, no audio.")
    return AudioStream(data, rebased, delay, padding, sample_rate)
