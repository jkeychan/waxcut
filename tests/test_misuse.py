"""Tests for how the library behaves when used incorrectly, not just when
fed malformed MP3 data (see test_frames.py for that side of things)."""

from pathlib import Path

import pytest

import waxcut


def test_frame_index_at_rejects_empty_frame_list():
    with pytest.raises(ValueError, match="empty"):
        waxcut.frame_index_at([], target_ms=0)


def test_slice_bytes_rejects_empty_frame_list():
    with pytest.raises(ValueError, match="empty"):
        waxcut.slice_bytes(b"", [], 0, 0)


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


def test_iter_frames_rejects_empty_bytes():
    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.iter_frames(b"")


@pytest.mark.parametrize(
    "garbage",
    [
        b"\x00" * 100,
        b"not even close to an mp3 file, just ascii text padded out" * 5,
        bytes(range(256)) * 4,
    ],
)
def test_iter_frames_rejects_various_garbage(garbage):
    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.iter_frames(garbage)
