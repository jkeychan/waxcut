"""Throughput benchmark for waxcut's pure-Python frame scanning.

Not part of the test suite or CI -- a standalone diagnostic for substantiating
(or catching regressions in) the "no ffmpeg, no subprocess" performance
tradeoff. Run with: uv run python bench/benchmark.py
"""

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from waxcut import load_audio_stream, split_at

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
ITERATIONS = 50
FFMPEG_ITERATIONS = 10  # subprocess spawn overhead dominates; fewer iterations needed
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def benchmark_fixture(path: Path) -> None:
    size_kb = path.stat().st_size / 1024

    start = time.perf_counter()
    for _ in range(ITERATIONS):
        stream = load_audio_stream(path)
    parse_elapsed = time.perf_counter() - start

    duration_ms = stream.playable_duration_ms
    timestamps = [duration_ms * i / 5 for i in range(1, 5)]

    start = time.perf_counter()
    for _ in range(ITERATIONS):
        split_at(stream, timestamps)
    split_elapsed = time.perf_counter() - start

    parse_ms = parse_elapsed / ITERATIONS * 1000
    split_ms = split_elapsed / ITERATIONS * 1000
    throughput_mb_s = (size_kb / 1024) / (parse_elapsed / ITERATIONS)

    print(
        f"{path.name:28s} {size_kb:8.1f} KB  "
        f"{len(stream.frames):6d} frames  "
        f"parse {parse_ms:6.3f} ms  "
        f"5-way split {split_ms:6.3f} ms  "
        f"{throughput_mb_s:7.1f} MB/s"
    )


def benchmark_two_way_split_vs_ffmpeg(path: Path) -> None:
    """Compare a realistic "split into two files on disk" operation:
    waxcut (parse + slice + write) vs ffmpeg -c copy (stream-copy, no
    re-encode -- the closest ffmpeg equivalent to waxcut's lossless claim),
    including ffmpeg's own process-spawn overhead.
    """
    stream = load_audio_stream(path)
    midpoint_ms = stream.playable_duration_ms / 2
    midpoint_s = midpoint_ms / 1000
    total_s = stream.playable_duration_ms / 1000

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        start = time.perf_counter()
        for i in range(FFMPEG_ITERATIONS):
            stream = load_audio_stream(path)
            parts = split_at(stream, [midpoint_ms])
            (tmp_dir / f"waxcut_{i}_a.mp3").write_bytes(parts[0])
            (tmp_dir / f"waxcut_{i}_b.mp3").write_bytes(parts[1])
        waxcut_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        for i in range(FFMPEG_ITERATIONS):
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-t",
                    f"{midpoint_s:.3f}",
                    "-c",
                    "copy",
                    str(tmp_dir / f"ffmpeg_{i}_a.mp3"),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-ss",
                    f"{midpoint_s:.3f}",
                    "-t",
                    f"{total_s - midpoint_s:.3f}",
                    "-c",
                    "copy",
                    str(tmp_dir / f"ffmpeg_{i}_b.mp3"),
                ],
                check=True,
            )
        ffmpeg_elapsed = time.perf_counter() - start

    waxcut_ms = waxcut_elapsed / FFMPEG_ITERATIONS * 1000
    ffmpeg_ms = ffmpeg_elapsed / FFMPEG_ITERATIONS * 1000
    print(
        f"{path.name:28s} 2-way split+write to disk:  "
        f"waxcut {waxcut_ms:7.3f} ms   ffmpeg -c copy {ffmpeg_ms:8.3f} ms   "
        f"waxcut is {ffmpeg_ms / waxcut_ms:5.1f}x faster"
    )


def main() -> None:
    fixtures = sorted(FIXTURES_DIR.glob("*.mp3"))
    print(f"Averaging over {ITERATIONS} iterations per fixture\n")
    for fixture in fixtures:
        benchmark_fixture(fixture)

    if not HAS_FFMPEG:
        print("\nffmpeg not found on PATH, skipping waxcut-vs-ffmpeg comparison")
        return

    print(f"\nwaxcut vs ffmpeg -c copy, averaging over {FFMPEG_ITERATIONS} iterations per fixture")
    print("(ffmpeg timing includes process-spawn overhead, same as real-world usage)\n")
    for fixture in fixtures:
        benchmark_two_way_split_vs_ffmpeg(fixture)


if __name__ == "__main__":
    main()
