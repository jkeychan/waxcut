"""waxcut: frame-accurate, lossless MP3 splitting and duration parsing in pure Python.

No ffmpeg, no subprocess, no decode step. Frames are located by scanning the
file's own MPEG frame headers, and splits are made by byte-copying whole
frames, so output is byte-identical to the corresponding span of the
source audio frames — just shorter.

    from waxcut import load_audio_stream, frame_index_at, slice_bytes

    stream = load_audio_stream(Path("song.mp3"))
    cut_at = frame_index_at(stream.frames, target_ms=90_000)
    first_half = slice_bytes(stream.data, stream.frames, 0, cut_at)

Splitting an album into tagged tracks:

    from waxcut import load_audio_stream, split_at, write_id3v2_tag

    stream = load_audio_stream(Path("album.mp3"))
    segments = split_at(stream, timestamps_ms=[cut1_ms, cut2_ms])
    for i, segment in enumerate(segments, start=1):
        tagged = write_id3v2_tag(segment, title=f"Track {i}", track=i)
        Path(f"track{i}.mp3").write_bytes(tagged)
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from waxcut.cue import CueSheetError, parse_cue_sheet
from waxcut.frames import (
    AudioStream,
    FileTooLargeError,
    Frame,
    UnsupportedMp3Error,
    frame_index_at,
    id3v2_size,
    join_frames,
    load_audio_stream,
    scan_frames,
    slice_bytes,
    split_at,
    total_duration_ms,
    write_id3v2_tag,
)

try:
    __version__ = _version("waxcut")
except PackageNotFoundError:
    # No installed-package metadata to read from — e.g. inside a frozen/
    # bundled executable (PyInstaller, as used by our own fuzz harness).
    # Obviously-wrong sentinel rather than a hardcoded number that would
    # silently drift out of sync with pyproject.toml on every release.
    __version__ = "0.0.0+unknown"

__all__ = [
    "AudioStream",
    "CueSheetError",
    "FileTooLargeError",
    "Frame",
    "UnsupportedMp3Error",
    "frame_index_at",
    "id3v2_size",
    "join_frames",
    "load_audio_stream",
    "parse_cue_sheet",
    "scan_frames",
    "slice_bytes",
    "split_at",
    "total_duration_ms",
    "write_id3v2_tag",
]
