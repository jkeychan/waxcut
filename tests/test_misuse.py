"""Tests for how the library behaves when used incorrectly, not just when
fed malformed MP3 data (see test_frames.py for that side of things)."""

import math
from pathlib import Path

import pytest

import waxcut


def test_frame_index_at_rejects_empty_frame_list():
    with pytest.raises(ValueError, match="empty"):
        waxcut.frame_index_at([], target_ms=0)


def test_frame_index_at_rejects_nan_target_ms():
    # NaN comparisons are always False, so `frame.start_ms > target_ms` never
    # trips -- without an explicit guard this silently walks to the last
    # frame index instead of raising, indistinguishable from a legitimate
    # clamp on real input.
    frames = [waxcut.Frame(offset=0, length=10, start_ms=0.0, duration_ms=26.0)]
    with pytest.raises(ValueError, match="NaN"):
        waxcut.frame_index_at(frames, math.nan)


def test_slice_bytes_rejects_empty_frame_list():
    with pytest.raises(ValueError, match="empty"):
        waxcut.slice_bytes(b"", [], 0, 0)


def test_slice_bytes_rejects_negative_indices():
    frames = [waxcut.Frame(offset=0, length=10, start_ms=0.0, duration_ms=26.0)]
    with pytest.raises(IndexError):
        waxcut.slice_bytes(b"\x00" * 10, frames, -1, 1)
    with pytest.raises(IndexError):
        waxcut.slice_bytes(b"\x00" * 10, frames, 0, -1)


FIXTURES = Path(__file__).parent / "fixtures"


def test_load_audio_stream_missing_file_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        waxcut.load_audio_stream(FIXTURES / "does_not_exist.mp3")


def test_load_audio_stream_directory_raises():
    with pytest.raises((IsADirectoryError, PermissionError)):
        waxcut.load_audio_stream(FIXTURES)


def test_load_audio_stream_non_mp3_file_raises_unsupported():
    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.load_audio_stream(FIXTURES / "not_an_mp3.txt")


def test_scan_frames_rejects_empty_bytes():
    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.scan_frames(b"")


@pytest.mark.parametrize(
    "garbage",
    [
        b"\x00" * 100,
        b"not even close to an mp3 file, just ascii text padded out" * 5,
        bytes(range(256)) * 4,
    ],
)
def test_scan_frames_rejects_various_garbage(garbage):
    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.scan_frames(garbage)


def test_id3v2_tag_claiming_huge_size_does_not_hang_or_crash():
    # Syncsafe 0x7F,0x7F,0x7F,0x7F = the maximum representable size
    # (~256MB) claimed on a file that's actually 14 bytes long.
    header = b"ID3\x04\x00\x00" + b"\x7f\x7f\x7f\x7f"
    tiny_file = header + b"\x00\x00\x00\x00"
    assert waxcut.id3v2_size(tiny_file) > len(tiny_file)

    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.scan_frames(tiny_file)


def test_scan_frames_rejects_input_over_the_size_limit(monkeypatch):
    # Patch the limit down rather than allocating a real 250MB buffer --
    # exercises the exact same guard without the CI cost.
    monkeypatch.setattr("waxcut.frames._MAX_FILE_SIZE_BYTES", 10)
    with pytest.raises(waxcut.FileTooLargeError):
        waxcut.scan_frames(b"\x00" * 11)


def test_scan_frames_allows_input_at_exactly_the_size_limit(monkeypatch):
    # FileTooLargeError subclasses UnsupportedMp3Error, so a plain
    # pytest.raises(UnsupportedMp3Error) here would pass even if the size
    # guard incorrectly fired at the boundary -- assert the exact type.
    monkeypatch.setattr("waxcut.frames._MAX_FILE_SIZE_BYTES", 10)
    with pytest.raises(waxcut.UnsupportedMp3Error) as exc_info:
        waxcut.scan_frames(b"\x00" * 10)  # not a valid MP3, but not oversized either
    assert exc_info.type is waxcut.UnsupportedMp3Error


def test_load_audio_stream_rejects_file_over_the_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("waxcut.frames._MAX_FILE_SIZE_BYTES", 10)
    oversized = tmp_path / "too_big.mp3"
    oversized.write_bytes(b"\x00" * 11)
    with pytest.raises(waxcut.FileTooLargeError):
        waxcut.load_audio_stream(oversized)
