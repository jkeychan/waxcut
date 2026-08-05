# bench/mmap_memory.py
"""Memory measurement for use_mmap=True vs. the default bytes-loading path.

Not part of the test suite or CI -- a standalone diagnostic, same convention
as bench/benchmark.py. Run with: uv run python bench/mmap_memory.py

Builds an adversarial file (minimum-size MPEG2.5 frames, maximum frame
count per byte) since that's the worst case for both the time cost use_mmap
is still subject to (see _MAX_MMAP_FILE_SIZE_BYTES's docstring) and the
memory cost use_mmap exists to avoid.
"""

import struct
import tempfile
import tracemalloc
from pathlib import Path

from waxcut import load_audio_stream
from waxcut.frames import _parse_header

# MPEG2.5, mono, no CRC, lowest bitrate (8kbps), 8000Hz, no padding --
# the smallest legal Layer III frame this codebase's header parser accepts.
_HEADER_BITS = "11111111111000110001100011000000"
_ADVERSARIAL_FRAME_COUNT = 300_000  # ~20MB total


def build_adversarial_file(path: Path) -> int:
    header_bytes = struct.pack(">I", int(_HEADER_BITS, 2))
    _, bitrate, sample_rate, padding = _parse_header(header_bytes + b"\x00" * 10, 0)
    length = int(72 * bitrate * 1000 / sample_rate) + padding
    frame = header_bytes + b"\x00" * (length - 4)
    data = frame * _ADVERSARIAL_FRAME_COUNT
    path.write_bytes(data)
    return len(data)


def measure(label: str, use_mmap: bool, path: Path) -> None:
    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()
    stream = load_audio_stream(path, use_mmap=use_mmap)
    snapshot_after = tracemalloc.take_snapshot()
    delta_bytes = sum(stat.size_diff for stat in snapshot_after.compare_to(snapshot_before, "lineno"))
    tracemalloc.stop()
    stream.close()
    print(
        f"{label:20s} {len(stream.frames):7d} frames   tracemalloc delta: {delta_bytes / 1024 / 1024:8.2f} MB"
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "adversarial.mp3"
        file_size = build_adversarial_file(path)
        print(
            f"Adversarial file: {file_size / 1024 / 1024:.1f} MB, "
            f"{_ADVERSARIAL_FRAME_COUNT} minimum-size frames\n"
        )
        measure("use_mmap=False", False, path)
        measure("use_mmap=True", True, path)


if __name__ == "__main__":
    main()
