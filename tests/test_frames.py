import contextlib
import itertools
import mmap
import shutil
import struct
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
        waxcut.scan_frames(b"this is not an mp3 file at all")


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
        waxcut.scan_frames(regression_file.read_bytes())


def test_split_at_matches_manual_frame_index_at_and_slice_bytes(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path)
    cut_at = stream.playable_duration_ms / 2

    segments = waxcut.split_at(stream, [cut_at])

    idx = waxcut.frame_index_at(stream.frames, cut_at)
    expected = [
        waxcut.slice_bytes(stream.data, stream.frames, 0, idx),
        waxcut.slice_bytes(stream.data, stream.frames, idx, len(stream.frames)),
    ]
    assert segments == expected


def test_split_at_no_timestamps_returns_whole_stream_as_one_segment(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path)
    (segment,) = waxcut.split_at(stream, [])
    assert segment == waxcut.slice_bytes(stream.data, stream.frames, 0, len(stream.frames))


def test_split_at_duplicate_timestamps_produce_an_empty_segment(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path)
    midpoint = stream.playable_duration_ms / 2
    segments = waxcut.split_at(stream, [midpoint, midpoint])
    assert len(segments) == 3
    assert segments[1] == b""


def test_split_at_sorts_out_of_order_timestamps_instead_of_duplicating_audio(fixture_path):
    # Unsorted cut points used to build overlapping index ranges, so the
    # segments covered some frames twice and join_frames produced a stream
    # longer than the original instead of reproducing it.
    stream = waxcut.load_audio_stream(fixture_path)
    duration = stream.playable_duration_ms
    whole = waxcut.slice_bytes(stream.data, stream.frames, 0, len(stream.frames))

    reversed_order = waxcut.split_at(stream, [duration * 0.75, duration * 0.25])
    ascending = waxcut.split_at(stream, [duration * 0.25, duration * 0.75])

    assert waxcut.join_frames(reversed_order) == whole
    assert sum(len(segment) for segment in reversed_order) == len(whole)
    # Input order only decides where the cuts land, never how many segments
    # come back, and the result is always in ascending stream order.
    assert len(reversed_order) == 3
    assert reversed_order == ascending


def test_split_at_normalizes_every_ordering_of_several_timestamps(fixture_path):
    # Two cut points can only be ordered or reversed, so a fix that merely
    # reverses a backwards pair satisfies the two-timestamp case above while
    # still building overlapping ranges for input that is unsorted in the
    # middle. Every permutation of three cut points must give byte-identical
    # segments, and each must still rejoin into exactly the original.
    stream = waxcut.load_audio_stream(fixture_path)
    duration = stream.playable_duration_ms
    whole = waxcut.slice_bytes(stream.data, stream.frames, 0, len(stream.frames))
    cuts = [duration * 0.25, duration * 0.5, duration * 0.75]

    ascending = waxcut.split_at(stream, cuts)
    for ordering in itertools.permutations(cuts):
        segments = waxcut.split_at(stream, list(ordering))
        assert len(segments) == len(cuts) + 1
        assert waxcut.join_frames(segments) == whole
        assert sum(len(segment) for segment in segments) == len(whole)
        assert segments == ascending


def test_split_at_repeated_timestamps_keep_one_segment_per_cut(fixture_path):
    # Sorting must not collapse equal indices: N identical cut points still
    # return N + 1 segments, all but the first and last empty.
    stream = waxcut.load_audio_stream(fixture_path)
    midpoint = stream.playable_duration_ms / 2
    whole = waxcut.slice_bytes(stream.data, stream.frames, 0, len(stream.frames))

    segments = waxcut.split_at(stream, [midpoint] * 4)

    assert len(segments) == 5
    assert segments[1:4] == [b"", b"", b""]
    assert waxcut.join_frames(segments) == whole


def test_join_frames_reverses_split_at(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path)
    whole = waxcut.slice_bytes(stream.data, stream.frames, 0, len(stream.frames))
    duration = stream.playable_duration_ms
    segments = waxcut.split_at(stream, [duration * 0.3, duration * 0.6])
    assert waxcut.join_frames(segments) == whole


def test_join_frames_empty_list_returns_empty_bytes():
    assert waxcut.join_frames([]) == b""


def _mpeg1_stereo_128kbps_header(*, protection_bit: int) -> bytes:
    """A valid MPEG1 Layer III, 128kbps, 44100Hz, stereo frame header."""
    sync = 0x7FF << 21
    version = 0b11 << 19  # MPEG1
    layer = 0b01 << 17  # Layer III
    protection = protection_bit << 16
    bitrate_idx = 9 << 12  # 128kbps
    sample_rate_idx = 0b00 << 10  # 44100Hz
    channel_mode = 0b00 << 6  # stereo
    header = sync | version | layer | protection | bitrate_idx | sample_rate_idx | channel_mode
    return struct.pack(">I", header)


def test_lame_gapless_tag_found_when_frame_is_crc_protected(tmp_path):
    # A CRC-protected frame (protection_bit=0) has 2 extra bytes between the
    # header and side info -- the LAME/Xing tag offset must account for them
    # or gapless delay/padding silently fails to be detected.
    header = _mpeg1_stereo_128kbps_header(protection_bit=0)
    frame_length = 417  # 144 * 128000 / 44100, truncated, no padding

    crc = b"\x00\x00"
    side_info = b"\x00" * 32
    tag = b"Xing"
    flags = b"\x00\x00\x00\x00"  # no optional Xing fields present
    version_string = b"LAME3.100"  # 9 bytes, must start with b"LAME"
    unused = b"\x00" * (21 - len(version_string))
    delay, padding = 500, 1000
    delay_padding = bytes(
        [
            (delay >> 4) & 0xFF,
            ((delay & 0xF) << 4) | ((padding >> 8) & 0xF),
            padding & 0xFF,
        ]
    )

    frame_one = header + crc + side_info + tag + flags + version_string + unused + delay_padding
    frame_one += b"\x00" * (frame_length - len(frame_one))
    assert len(frame_one) == frame_length

    frame_two = _mpeg1_stereo_128kbps_header(protection_bit=1) + b"\x00" * (frame_length - 4)

    synthetic_mp3 = tmp_path / "crc_protected.mp3"
    synthetic_mp3.write_bytes(frame_one + frame_two)

    stream = waxcut.load_audio_stream(synthetic_mp3)
    assert stream.encoder_delay_samples == delay
    assert stream.encoder_padding_samples == padding


def _mpeg2_stereo_8kbps_header(*, sample_rate_idx: int) -> bytes:
    """A valid MPEG2 Layer III, 8kbps, stereo, CRC-free frame header.

    The smallest frames the format allows -- short enough that a Xing tag
    sits near the very end of the frame, which is what makes the truncated
    reads below reachable at all.
    """
    sync = 0x7FF << 21
    version = 0b10 << 19  # MPEG2
    layer = 0b01 << 17  # Layer III
    protection = 1 << 16  # no CRC
    bitrate_idx = 1 << 12  # 8kbps
    channel_mode = 0b00 << 6  # stereo
    header = sync | version | layer | protection | bitrate_idx | (sample_rate_idx << 10) | channel_mode
    return struct.pack(">I", header)


def test_truncated_xing_flags_word_raises_unsupported_not_struct_error(tmp_path):
    # 8kbps @ 22050Hz gives a 26-byte frame; the Xing tag starts at byte 21
    # (4 header + 17 side info), so the tag itself fits but the 4-byte flags
    # word that follows it runs past EOF. Reading it unguarded leaked a raw
    # struct.error out of load_audio_stream.
    frame = _mpeg2_stereo_8kbps_header(sample_rate_idx=0b00) + b"\x00" * 17 + b"Xing"
    frame += b"\x00" * (26 - len(frame))
    assert len(frame) == 26

    truncated = tmp_path / "truncated_xing.mp3"
    truncated.write_bytes(frame)

    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.load_audio_stream(truncated)


def test_vbr_tag_straddling_the_frame_end_is_not_treated_as_a_vbr_header(tmp_path):
    # 8kbps @ 24000Hz gives a 24-byte frame, so the 4-byte probe at offset 21
    # runs one byte past the frame's own end. Bounding that probe against the
    # whole buffer instead of the frame let trailing bytes complete a "Xing"
    # that isn't in this frame at all -- and the real audio frame was then
    # discarded as encoder metadata.
    frame = _mpeg2_stereo_8kbps_header(sample_rate_idx=0b01) + b"\x00" * 17 + b"Xin"
    assert len(frame) == 24
    straddling = tmp_path / "straddling_tag.mp3"
    straddling.write_bytes(frame + b"g" + b"\x00" * 40)  # trailing junk completes "Xing"

    stream = waxcut.load_audio_stream(straddling)
    assert len(stream.frames) == 1
    assert stream.frames[0].offset == 0


def test_lame_gapless_tag_past_frame_end_is_not_read(tmp_path):
    # Same 26-byte MPEG2/stereo/8kbps/22050Hz/no-CRC geometry as the
    # straddling-tag test above: the Xing tag (offset 21, 4 bytes) fits
    # entirely inside the frame, but the LAME extension that follows it
    # (flags word + "LAME" version string + delay/padding) does not -- it
    # needs 32 more bytes than the 1 remaining in the frame. Bounding the
    # read against the whole buffer instead of the frame let bytes from
    # this junk region (and potentially a second real frame) be read and
    # decoded as if they were this frame's gapless delay/padding.
    frame_one = _mpeg2_stereo_8kbps_header(sample_rate_idx=0b00) + b"\x00" * 17 + b"Xing"
    frame_one += b"\x00" * (26 - len(frame_one))
    assert len(frame_one) == 26

    flags = b"\x00\x00\x00\x00"  # combines with frame_one's last byte to read as flags=0
    version_string = b"LAME3.100"
    unused = b"\x00" * (21 - len(version_string))
    delay, padding = 321, 654
    delay_padding = bytes(
        [
            (delay >> 4) & 0xFF,
            ((delay & 0xF) << 4) | ((padding >> 8) & 0xF),
            padding & 0xFF,
        ]
    )
    # This junk sits entirely past frame_one's end (offset 26+), shaped so a
    # buggy read bounded only by len(data) would decode it as a genuine LAME
    # gapless tag -- exactly the bytes _parse_lame_gapless must not reach.
    junk = flags[1:] + version_string + unused + delay_padding

    frame_two = _mpeg2_stereo_8kbps_header(sample_rate_idx=0b00) + b"\x00" * 22
    assert len(frame_two) == 26

    crafted = tmp_path / "lame_tag_past_frame_end.mp3"
    crafted.write_bytes(frame_one + junk + frame_two)

    stream = waxcut.load_audio_stream(crafted)
    assert stream.encoder_delay_samples == 0
    assert stream.encoder_padding_samples == 0


def test_vbri_tag_found_at_fixed_offset_for_non_mpeg1_stereo_frame(tmp_path):
    # The VBRI header sits at a fixed 36-byte offset from the frame start
    # (unlike Xing/Info, which follow the side info and so move around with
    # channel mode/CRC). Use an MPEG1 mono, CRC-protected frame: the
    # Xing/Info side-info formula (4 + crc + side_info = 4 + 2 + 17 = 23)
    # lands well short of byte 36, so a VBRI tag placed at the real,
    # fixed offset is only found if it's probed there specifically.
    sync = 0x7FF << 21
    version = 0b11 << 19  # MPEG1
    layer = 0b01 << 17  # Layer III
    protection = 0 << 16  # CRC present
    bitrate_idx = 9 << 12  # 128kbps
    sample_rate_idx = 0b00 << 10  # 44100Hz
    channel_mode = 0b11 << 6  # mono
    header = struct.pack(
        ">I", sync | version | layer | protection | bitrate_idx | sample_rate_idx | channel_mode
    )
    frame_length = 417  # 144 * 128000 / 44100, truncated, no padding

    crc = b"\x00\x00"
    padding_to_vbri = b"\x00" * (36 - len(header) - len(crc))
    frame_one = header + crc + padding_to_vbri + b"VBRI"
    frame_one += b"\x00" * (frame_length - len(frame_one))
    assert len(frame_one) == frame_length

    frame_two = _mpeg1_stereo_128kbps_header(protection_bit=1) + b"\x00" * (frame_length - 4)

    synthetic_mp3 = tmp_path / "vbri_mono_crc.mp3"
    synthetic_mp3.write_bytes(frame_one + frame_two)

    stream = waxcut.load_audio_stream(synthetic_mp3)
    assert len(stream.frames) == 1
    assert stream.frames[0].offset == frame_length


def test_load_audio_stream_use_mmap_produces_identical_frames(fixture_path):
    normal = waxcut.load_audio_stream(fixture_path)
    with waxcut.load_audio_stream(fixture_path, use_mmap=True) as mmapped:
        assert len(mmapped.frames) == len(normal.frames)
        assert list(mmapped.frames) == list(normal.frames)
        assert mmapped.sample_rate == normal.sample_rate
        assert mmapped.encoder_delay_samples == normal.encoder_delay_samples
        assert mmapped.encoder_padding_samples == normal.encoder_padding_samples
        assert bytes(mmapped.data[:]) == normal.data
        assert isinstance(mmapped.data, mmap.mmap)


def test_load_audio_stream_use_mmap_split_output_matches_non_mmap(fixture_path):
    normal = waxcut.load_audio_stream(fixture_path)
    with waxcut.load_audio_stream(fixture_path, use_mmap=True) as mmapped:
        cut_idx = len(mmapped.frames) // 2
        mmap_first_half = waxcut.slice_bytes(mmapped.data, mmapped.frames, 0, cut_idx)
        normal_first_half = waxcut.slice_bytes(normal.data, normal.frames, 0, cut_idx)
        assert mmap_first_half == normal_first_half
        assert isinstance(mmap_first_half, bytes)
