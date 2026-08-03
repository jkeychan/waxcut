"""waxcut: frame-accurate, lossless MP3 splitting and duration parsing in pure Python.

No ffmpeg, no subprocess, no decode step. Frames are located by scanning the
file's own MPEG frame headers, and splits are made by byte-copying whole
frames, so output is bit-identical to the source — just shorter.

    from waxcut import load_audio_stream, frame_index_at, slice_bytes

    stream = load_audio_stream(Path("song.mp3"))
    cut_at = frame_index_at(stream.frames, target_ms=90_000)
    first_half = slice_bytes(stream.data, stream.frames, 0, cut_at)
"""

from waxcut.frames import (
    AudioStream,
    Frame,
    UnsupportedMp3Error,
    frame_index_at,
    id3v2_size,
    iter_frames,
    load_audio_stream,
    slice_bytes,
    total_duration_ms,
)

__version__ = "0.1.0"

__all__ = [
    "AudioStream",
    "Frame",
    "UnsupportedMp3Error",
    "frame_index_at",
    "id3v2_size",
    "iter_frames",
    "load_audio_stream",
    "slice_bytes",
    "total_duration_ms",
]
