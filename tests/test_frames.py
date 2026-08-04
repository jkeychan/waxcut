import contextlib
import itertools
import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.mp3 import MP3

import waxcut

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

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(params=FIXTURE_NAMES)
def fixture_path(request) -> Path:
    return FIXTURES / request.param


def test_duration_matches_mutagen(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path)
    expected_ms = MP3(fixture_path).info.length * 1000
    assert stream.playable_duration_ms == pytest.approx(expected_ms, abs=1.0)


def test_frames_are_contiguous(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path)
    for prev, curr in zip(stream.frames, stream.frames[1:], strict=False):
        assert curr.offset == prev.offset + prev.length


def test_frame_start_ms_is_monotonic_and_starts_at_zero(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path)
    assert stream.frames[0].start_ms == 0
    for prev, curr in zip(stream.frames, stream.frames[1:], strict=False):
        assert curr.start_ms > prev.start_ms


def test_split_byte_completeness(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path)
    midpoint = waxcut.frame_index_at(stream.frames, stream.playable_duration_ms / 2)
    part1 = waxcut.slice_bytes(stream.data, stream.frames, 0, midpoint)
    part2 = waxcut.slice_bytes(stream.data, stream.frames, midpoint, len(stream.frames))
    whole = waxcut.slice_bytes(stream.data, stream.frames, 0, len(stream.frames))
    assert len(part1) + len(part2) == len(whole)
    assert part1 + part2 == whole


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available for independent decode validation")
def test_split_output_is_valid_mp3(fixture_path, tmp_path):
    stream = waxcut.load_audio_stream(fixture_path)
    duration = stream.playable_duration_ms
    points = [duration / 3, 2 * duration / 3]
    idxs = [0, *[waxcut.frame_index_at(stream.frames, p) for p in points], len(stream.frames)]

    for i, (start, end) in enumerate(itertools.pairwise(idxs)):
        if start >= end:
            continue
        out_path = tmp_path / f"part{i}.mp3"
        out_path.write_bytes(waxcut.slice_bytes(stream.data, stream.frames, start, end))

        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(out_path), "-f", "null", "-"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stderr == ""


def test_id3v2_size_no_tag():
    assert waxcut.id3v2_size(b"\xff\xfb\x00\x00rest of file") == 0


def test_id3v2_size_with_tag():
    # 10-byte ID3v2 header + syncsafe size of 5 (0x05) = 15 bytes total.
    header = b"ID3\x04\x00\x00\x00\x00\x00\x05"
    assert waxcut.id3v2_size(header + b"12345" + b"rest") == 15


def test_unsupported_file_raises():
    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.iter_frames(b"this is not an mp3 file at all")


def test_frame_index_at_clamps_to_range(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path)
    assert waxcut.frame_index_at(stream.frames, -100) == 0
    assert waxcut.frame_index_at(stream.frames, 10**9) == len(stream.frames) - 1


def test_ffmpeg_native_encoder_does_not_produce_false_gapless_values():
    # cbr_stereo.mp3/cbr_mono.mp3 are encoded with ffmpeg's native "Lavc..."
    # tagged encoder, not real LAME — regression test for misreading that
    # tag's bytes as if they were LAME's gapless delay/padding fields.
    for name in ("cbr_stereo.mp3", "cbr_mono.mp3"):
        stream = waxcut.load_audio_stream(FIXTURES / name)
        assert stream.encoder_delay_samples == 0
        assert stream.encoder_padding_samples == 0


REGRESSION_FIXTURES = Path(__file__).parent / "fixtures" / "regression"


@pytest.mark.parametrize(
    "regression_file",
    sorted(REGRESSION_FIXTURES.glob("*.bin")) if REGRESSION_FIXTURES.exists() else [],
    ids=lambda p: p.name,
)
def test_regression_corpus_does_not_crash(regression_file):
    with contextlib.suppress(waxcut.UnsupportedMp3Error):
        waxcut.iter_frames(regression_file.read_bytes())
