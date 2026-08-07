"""Tests for how the library behaves when used incorrectly, not just when
fed malformed MP3 data (see test_frames.py for that side of things)."""

import math
import mmap
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
FIXTURE_NAMES = [
    "cbr_stereo.mp3",
    "cbr_mono.mp3",
    "vbr_stereo.mp3",
    "lame_vbr_stereo.mp3",
    "mono_8khz.mp3",
    "joint_stereo_vbr.mp3",
    "with_id3v1_trailer.mp3",
]


@pytest.fixture(params=FIXTURE_NAMES)
def fixture_path(request) -> Path:
    return FIXTURES / request.param


def test_frames_reversed_slice_is_empty_like_a_list(fixture_path):
    # frames[5:2] built a view with stop < start, so __len__ returned a
    # negative number and CPython turned it into a bare ValueError. A real
    # list just returns [], and Frames documents list-equivalent slicing.
    frames = waxcut.load_audio_stream(fixture_path).frames
    assert len(frames) > 5

    reversed_slice = frames[5:2]
    assert len(reversed_slice) == 0
    assert list(reversed_slice) == []
    assert not reversed_slice
    # and a reversed slice of an already-sliced view behaves the same
    assert len(frames[1:][4:1]) == 0


def test_frames_every_reversed_slice_form_matches_the_equivalent_list_slice(fixture_path):
    # The clamp has to hold for every spelling of a backwards range, not just
    # a literal frames[5:2]: negative bounds and an explicit step of 1 reach
    # it through slice.indices() rather than directly.
    frames = waxcut.load_audio_stream(fixture_path).frames
    as_list = list(frames)
    count = len(frames)
    assert count > 5

    for start, stop in ((5, 2), (-1, -5), (count, 0), (-1, 0), (10**9, 0)):
        view = frames[start:stop]
        expected = as_list[start:stop]
        assert expected == []
        assert len(view) == 0
        assert list(view) == []
        assert not view
        with pytest.raises(IndexError):
            view[0]

    # an explicit step of 1 is still a supported slice, unlike step != 1
    assert len(frames[5:2:1]) == 0
    # and slicing or rebasing an already-empty view stays empty
    assert len(frames[5:2][0:5]) == 0
    assert len(frames[5:2].rebase(10.0)) == 0


def test_frames_forward_slicing_still_matches_list_semantics(fixture_path):
    # _view is shared by every slice, so the reversed-range clamp must not
    # disturb ordinary forward slicing, including negative and out-of-range
    # bounds.
    frames = waxcut.load_audio_stream(fixture_path).frames
    as_list = list(frames)
    count = len(frames)

    bounds = [None, 0, 1, count // 2, count - 1, count, count + 50, -1, -count, -count - 50]
    for start in bounds:
        for stop in bounds:
            view = frames[start:stop]
            expected = as_list[start:stop]
            assert len(view) == len(expected)
            assert list(view) == expected
            assert bool(view) == bool(expected)


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


def test_write_id3v2_tag_rejects_track_below_one():
    with pytest.raises(ValueError, match="track"):
        waxcut.write_id3v2_tag(b"data", track=0)
    with pytest.raises(ValueError, match="track"):
        waxcut.write_id3v2_tag(b"data", track=-5)


def test_write_id3v2_tag_rejects_data_with_a_pre_existing_id3v2_tag():
    # The most realistic double-call scenario: re-tagging output that was
    # already tagged (e.g. re-running a pipeline). Stacking a second tag
    # would corrupt frame scanning -- see id3v2_size, which only ever
    # accounts for the outermost tag.
    already_tagged = waxcut.write_id3v2_tag(b"REST", title="X")
    with pytest.raises(ValueError, match="leading ID3v2 tag"):
        waxcut.write_id3v2_tag(already_tagged, title="Y")


def test_write_id3v2_tag_accepts_memoryview_input():
    # slice_bytes always returns real bytes today, but write_id3v2_tag
    # coerces via bytes(data) defensively -- see the plan doc's
    # Compatibility section (issue #28, streaming/mmap support) for why.
    tagged = waxcut.write_id3v2_tag(memoryview(b"payload"), title="T")
    assert tagged.endswith(b"payload")
    assert isinstance(tagged, bytes)


def test_write_id3v2_tag_syncsafe_guard_rejects_oversized_frame_payload():
    # A pathologically large title is real user-input misuse (a caller
    # passing e.g. an entire file's contents as "title" by mistake), not
    # a scenario the format itself can represent -- syncsafe size is
    # capped at 2**28 - 1.
    huge_title = "x" * (1 << 28)
    with pytest.raises(ValueError, match=r"syncsafe|fit"):
        waxcut.write_id3v2_tag(b"data", title=huge_title)


@pytest.mark.parametrize("forbidden_char", ["\x00", "\r", "\n"])
def test_write_id3v2_tag_rejects_nul_cr_lf_in_text_fields(forbidden_char):
    # NUL/CR/LF pass through Latin-1/UTF-16 text encoding unremarked, so
    # displayed content (a NUL-truncated string, or CR/LF making a
    # single-line field look multi-line) could silently differ from what
    # was actually stored -- reject rather than silently strip.
    with pytest.raises(ValueError, match="cannot contain"):
        waxcut.write_id3v2_tag(b"data", title=f"a{forbidden_char}b")
    with pytest.raises(ValueError, match="cannot contain"):
        waxcut.write_id3v2_tag(b"data", artist=f"a{forbidden_char}b")


def test_audio_stream_close_is_a_noop_without_mmap(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path)
    stream.close()  # must not raise
    # data is still fully valid bytes after close() on the non-mmap path
    assert len(stream.data) > 0


def test_audio_stream_context_manager_closes_mmap_on_exit(fixture_path):
    with waxcut.load_audio_stream(fixture_path, use_mmap=True) as stream:
        assert not stream.data.closed
    assert stream.data.closed


def test_audio_stream_mmap_close_is_idempotent(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path, use_mmap=True)
    stream.close()
    stream.close()  # must not raise on a second close


def test_audio_stream_context_manager_closes_on_exception(fixture_path):
    with pytest.raises(RuntimeError), waxcut.load_audio_stream(fixture_path, use_mmap=True) as stream:
        raise RuntimeError("boom")
    assert stream.data.closed


def test_load_audio_stream_mmap_allows_files_larger_than_the_default_cap(monkeypatch):
    # Confirms use_mmap=True is governed by the separate, larger cap, not
    # the default 250MB one -- shrink both caps so the test doesn't need to
    # allocate real multi-GB files.
    monkeypatch.setattr("waxcut.frames._MAX_FILE_SIZE_BYTES", 10)
    monkeypatch.setattr("waxcut.frames._MAX_MMAP_FILE_SIZE_BYTES", 1_000_000)
    fixture = FIXTURES / "cbr_stereo.mp3"  # comfortably between 10 bytes and 1MB
    with pytest.raises(waxcut.FileTooLargeError):
        waxcut.load_audio_stream(fixture)  # default cap still applies
    with waxcut.load_audio_stream(fixture, use_mmap=True) as stream:  # mmap cap doesn't
        assert len(stream.frames) > 0


def test_load_audio_stream_mmap_rejects_input_over_its_own_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("waxcut.frames._MAX_MMAP_FILE_SIZE_BYTES", 10)
    oversized = tmp_path / "too_big.mp3"
    oversized.write_bytes(b"\x00" * 11)
    with pytest.raises(waxcut.FileTooLargeError):
        waxcut.load_audio_stream(oversized, use_mmap=True)


def test_load_audio_stream_mmap_empty_file_raises_unsupported_not_valueerror(tmp_path):
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.load_audio_stream(empty, use_mmap=True)
    # and the non-mmap path already does the same -- lock in parity
    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.load_audio_stream(empty, use_mmap=False)


def test_load_audio_stream_mmap_garbage_input_closes_file_and_mmap_on_parse_failure(monkeypatch):
    # Regression test for the mmap error path: a parse failure on
    # attacker-controlled bytes must still close the mmap and file handle
    # before re-raising (see load_audio_stream's `except Exception` block).
    # Spying on Path.open/mmap.mmap to hold our own strong references is
    # essential here -- without it, CPython's refcounting GC closes the
    # mmap/file via their own __del__ as soon as load_audio_stream's local
    # variables go out of scope, which would make a naive
    # "no fd leaked" check pass even with the explicit close() calls
    # removed (verified empirically).
    captured: dict[str, object] = {}
    real_open = Path.open
    real_mmap_ctor = mmap.mmap

    def spy_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        captured["file_handle"] = handle
        return handle

    def spy_mmap(*args, **kwargs):
        mapped = real_mmap_ctor(*args, **kwargs)
        captured["mmap"] = mapped
        return mapped

    monkeypatch.setattr(Path, "open", spy_open)
    monkeypatch.setattr(mmap, "mmap", spy_mmap)

    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.load_audio_stream(FIXTURES / "not_an_mp3.txt", use_mmap=True)

    assert captured["file_handle"].closed
    assert captured["mmap"].closed
