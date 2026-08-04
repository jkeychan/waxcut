"""Tests for how the library behaves when used incorrectly, not just when
fed malformed MP3 data (see test_frames.py for that side of things)."""

import pytest

import waxcut


def test_frame_index_at_rejects_empty_frame_list():
    with pytest.raises(ValueError, match="empty"):
        waxcut.frame_index_at([], target_ms=0)


def test_slice_bytes_rejects_empty_frame_list():
    with pytest.raises(ValueError, match="empty"):
        waxcut.slice_bytes(b"", [], 0, 0)
