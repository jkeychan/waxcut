# bench/security_claims.py
"""Reproduces the specific numbers cited in SECURITY.md / site/docs/security.md.

Not part of the test suite or CI -- a standalone diagnostic, same convention
as bench/benchmark.py and bench/mmap_memory.py. Run with:

    uv run python bench/security_claims.py

Covers four claims:

1. Frames storage vs. an equivalent list[Frame], for a 10 MB adversarial
   file built from the smallest legal Layer III frame this codebase's
   header parser accepts (MPEG2, 8 kbps, 24000 Hz -- 24 bytes, smaller than
   the MPEG2.5/8000Hz construction bench/mmap_memory.py uses).
2. scan_frames throughput against a "valid-frame carpet" -- a file packed
   edge-to-edge with minimum-size MPEG2.5 frames, all of which parse
   successfully. This is the rate that governs how long a legitimate-shaped
   but adversarially dense file takes to scan.
3. scan_frames throughput against the true adversarial worst case -- a
   buffer that never forms a valid sync at all (a carpet of 0xFF bytes),
   forcing a failed header-parse attempt at every byte -- and how long
   _MAX_CONSECUTIVE_RESYNC_FAILURES takes to trip and abort the scan.
4. parse_cue_sheet's peak memory use for a large, valid multi-track cue
   sheet, relative to the input text size.

Numbers vary by hardware; see SECURITY.md for a specific run's results.
"""

import contextlib
import struct
import time
import tracemalloc

from waxcut import CueSheetError, parse_cue_sheet
from waxcut.frames import (
    _BITRATES_KBPS,
    _MAX_CONSECUTIVE_RESYNC_FAILURES,
    _SAMPLE_RATES,
    Frame,
    MpegVersion,
    UnsupportedMp3Error,
    _frame_length,
    scan_frames,
)


def _min_frame(version: MpegVersion) -> tuple[int, int, int]:
    """Return (frame_length, bitrate_idx, sample_rate_idx) minimized over a version's table."""
    best: tuple[int, int, int] | None = None
    for bidx, bitrate in enumerate(_BITRATES_KBPS[version]):
        if bitrate is None:
            continue
        for sidx, sr in enumerate(_SAMPLE_RATES[version]):
            if sr is None:
                continue
            length = _frame_length(version, bitrate, sr, 0)
            if best is None or length < best[0]:
                best = (length, bidx, sidx)
    if best is None:
        raise RuntimeError(f"no valid bitrate/sample-rate combination for {version}")
    return best


def _build_frame(version: MpegVersion, bidx: int, sidx: int, length: int, channel_mode: int = 0b00) -> bytes:
    version_bits = {MpegVersion.MPEG1: 0b11, MpegVersion.MPEG2: 0b10, MpegVersion.MPEG2_5: 0b00}[version]
    header = (
        0xFFE00000
        | (version_bits << 19)
        | (0b01 << 17)  # layer III
        | (1 << 16)  # protection_bit=1 -> no CRC
        | (bidx << 12)
        | (sidx << 10)
        | (channel_mode << 6)
    )
    header_bytes = struct.pack(">I", header)
    return header_bytes + b"\x00" * (length - 4)


def frames_storage_amplification() -> None:
    length, bidx, sidx = _min_frame(MpegVersion.MPEG2)
    frame = _build_frame(MpegVersion.MPEG2, bidx, sidx, length)
    target_bytes = int(10e6)  # decimal MB, matching the MB display below
    frame_count = target_bytes // length
    data = frame * frame_count

    tracemalloc.start()
    frames = scan_frames(data)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    frame_count_actual = len(frames)

    def _make_frame(i: int) -> Frame:
        return Frame(offset=i * length, length=length, start_ms=float(i), duration_ms=1.0)

    tracemalloc.start()
    snap_before = tracemalloc.take_snapshot()
    list_frames = [_make_frame(i) for i in range(frame_count_actual)]
    snap_after = tracemalloc.take_snapshot()
    list_delta = sum(s.size_diff for s in snap_after.compare_to(snap_before, "lineno"))
    tracemalloc.stop()
    del list_frames

    frames_bpf = peak / frame_count_actual
    list_bpf = list_delta / frame_count_actual
    print(f"1. Frames storage amplification (min {length}-byte frames, {len(data) / 1e6:.1f} MB input)")
    print(f"   Frames: {peak / 1e6:.2f} MB peak, {peak / len(data):.3f}x, {frames_bpf:.1f} B/frame")
    list_amp = list_delta / len(data)
    print(f"   list[Frame]: {list_delta / 1e6:.2f} MB, {list_amp:.3f}x, {list_bpf:.1f} B/frame\n")


def valid_frame_carpet_throughput() -> None:
    length, bidx, sidx = _min_frame(MpegVersion.MPEG2_5)
    frame = _build_frame(MpegVersion.MPEG2_5, bidx, sidx, length, channel_mode=0b11)
    target_bytes = int(20.6e6)  # decimal MB, matching the MB/s display below
    data = frame * (target_bytes // length)

    scan_frames(data, max_size=len(data) + 1)  # warm up
    n = 5
    start = time.perf_counter()
    for _ in range(n):
        scan_frames(data, max_size=len(data) + 1)
    elapsed = (time.perf_counter() - start) / n
    print(f"2. Valid-frame-carpet throughput ({len(data) / 1e6:.1f} MB, {length}-byte MPEG2.5 frames)")
    print(f"   {elapsed * 1000:.1f} ms/scan, {len(data) / elapsed / 1e6:.1f} MB/s\n")


def worst_case_no_sync_timing() -> None:
    buf = b"\xff" * 5_000_000
    start = time.perf_counter()
    with contextlib.suppress(UnsupportedMp3Error):
        scan_frames(buf, max_size=len(buf) + 1)
    elapsed = time.perf_counter() - start
    print(f"3. All-0xFF worst-case scan ({len(buf) / 1e6:.1f} MB, never forms a valid sync)")
    print(
        f"   Raised after {elapsed * 1000:.1f} ms "
        f"({_MAX_CONSECUTIVE_RESYNC_FAILURES} consecutive failed resyncs, "
        f"{_MAX_CONSECUTIVE_RESYNC_FAILURES / elapsed / 1e6:.2f} M attempts/s)\n"
    )


def cue_sheet_amplification() -> None:
    n = 99_999
    lines = ['FILE "album.mp3" MP3']
    for i in range(1, n + 1):
        total_seconds = i * 2
        mm, ss = divmod(total_seconds, 60)
        lines.append(f"  TRACK {i % 99 + 1:02d} AUDIO")
        lines.append(f'    TITLE "Track {i}"')
        lines.append(f"    INDEX 01 {mm:02d}:{ss:02d}:00")
    text = "\n".join(lines) + "\n"
    text_bytes = len(text.encode())

    tracemalloc.start()
    try:
        result = parse_cue_sheet(text)
    except CueSheetError as e:
        tracemalloc.stop()
        raise SystemExit(f"cue sheet generation produced invalid input: {e}") from e
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"4. parse_cue_sheet peak memory ({text_bytes / 1e6:.2f} MB input, {len(result)} timestamps)")
    print(f"   {peak / 1e6:.2f} MB peak, {peak / text_bytes:.2f}x amplification\n")


def main() -> None:
    frames_storage_amplification()
    valid_frame_carpet_throughput()
    worst_case_no_sync_timing()
    cue_sheet_amplification()


if __name__ == "__main__":
    main()
