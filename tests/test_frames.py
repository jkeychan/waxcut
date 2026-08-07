import contextlib
import dataclasses
import itertools
import mmap
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from mutagen.mp3 import MP3

import waxcut
from waxcut import Frames

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_NAMES = [
    "cbr_stereo.mp3",
    "cbr_mono.mp3",
    "vbr_stereo.mp3",
    "lavc_vbr_stereo.mp3",
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


def test_id3v2_size_includes_footer_when_flag_set():
    # Flags byte 0x10 (bit 4, ID3v2.4-only) means a 10-byte footer mirrors
    # the header at the end of the tag -- its length isn't part of the
    # syncsafe size field and must be added on top of it.
    header = b"ID3\x04\x00\x10\x00\x00\x00\x05"
    assert waxcut.id3v2_size(header + b"12345" + b"rest") == 25


def test_id3v2_size_ignores_footer_flag_bit_on_pre_v2_4_tags():
    # I6 regression: bit 0x10 is ID3v2.4-only -- it's reserved (must be
    # zero) in v2.2/v2.3, so a v2.3 tag with that bit set anyway (major
    # version byte 0x03, not 0x04) must not have 10 bytes skipped that
    # were never actually written. Found by a fresh adversarial code
    # review: the version byte (data[3]) was never checked, so this
    # produced an offset 10 bytes short of the real first audio frame for
    # any tag setting that reserved bit.
    header = b"ID3\x03\x00\x10\x00\x00\x00\x05"
    assert waxcut.id3v2_size(header + b"12345" + b"rest") == 15


def test_frames_is_importable_from_top_level_waxcut():
    assert waxcut.Frames is Frames


def test_unsupported_mp3_error_is_a_waxcut_error():
    assert issubclass(waxcut.UnsupportedMp3Error, waxcut.WaxcutError)


def test_unsupported_file_raises():
    with pytest.raises(waxcut.UnsupportedMp3Error):
        waxcut.scan_frames(b"this is not an mp3 file at all")


def test_frame_index_at_clamps_to_range(fixture_path):
    stream = waxcut.load_audio_stream(fixture_path)
    assert waxcut.frame_index_at(stream.frames, -100) == 0
    assert waxcut.frame_index_at(stream.frames, 10**9) == len(stream.frames) - 1


def test_audio_stream_equality_and_hash_are_identity_based(fixture_path):
    # AudioStream is frozen but must not have content-based equality: two
    # AudioStreams sharing the very same underlying data/frames objects
    # still must not compare equal. Two independent load_audio_stream()
    # calls already differ (their `frames` field differs by identity, since
    # Frames has no __eq__ of its own) even under the old field-wise
    # dataclass eq, so that alone wouldn't catch a regression back to
    # eq=True -- sharing the same data/frames objects here is what actually
    # exercises the fix.
    stream1 = waxcut.load_audio_stream(fixture_path)
    stream2 = waxcut.AudioStream(
        stream1.data,
        stream1.frames,
        stream1.encoder_delay_samples,
        stream1.encoder_padding_samples,
        stream1.sample_rate,
    )
    assert stream1 != stream2
    assert hash(stream1) != hash(stream2)


def test_ffmpeg_native_encoder_does_not_produce_false_gapless_values():
    # cbr_stereo.mp3/cbr_mono.mp3/lavc_vbr_stereo.mp3 are encoded with
    # ffmpeg's native "Lavc..." tagged encoder, not real LAME — regression
    # test for misreading that tag's bytes as if they were LAME's gapless
    # delay/padding fields.
    for name in ("cbr_stereo.mp3", "cbr_mono.mp3", "lavc_vbr_stereo.mp3"):
        stream = waxcut.load_audio_stream(FIXTURES / name)
        assert stream.encoder_delay_samples == 0
        assert stream.encoder_padding_samples == 0


def test_real_lame_encode_reports_known_gapless_delay_and_padding():
    # lame_vbr_stereo.mp3 is a genuine `lame --vbr-new -q 4` encode (LAME
    # 3.100) of a 2-second sine wave -- unlike every synthetic-header test
    # above, its bytes are exactly what a real encoder wrote, not a
    # hand-crafted layout. 576 is a widely-documented LAME constant (its
    # MDCT filter's fixed encoder delay, independent of quality/bitrate
    # settings); the padding value below was read back from this specific
    # file's own bytes (not assumed) via load_audio_stream and cross-
    # checked against mutagen's independently-computed duration, which
    # matches the source's true 2000ms length almost exactly once this
    # delay+padding is trimmed -- see test_duration_matches_mutagen.
    stream = waxcut.load_audio_stream(FIXTURES / "lame_vbr_stereo.mp3")
    assert stream.encoder_delay_samples == 576
    assert stream.encoder_padding_samples == 1080


def test_playable_duration_ms_trims_gapless_delay_and_padding():
    # None of the real fixtures have nonzero delay/padding, so replacing
    # playable_duration_ms's body with `return self.duration_ms` still
    # passes every other test in this file -- this needs synthetic values.
    # dataclasses.replace() on a real load_audio_stream() result keeps
    # everything else (frames, data, sample_rate) genuine and overrides
    # only the two gapless fields under test.
    stream = waxcut.load_audio_stream(FIXTURES / "cbr_stereo.mp3")
    delay, padding = 1000, 1000
    trimmed = dataclasses.replace(stream, encoder_delay_samples=delay, encoder_padding_samples=padding)

    expected_trim_ms = (delay + padding) / stream.sample_rate * 1000
    assert expected_trim_ms < stream.duration_ms  # sanity check: not exercising the clamp here
    assert trimmed.playable_duration_ms == pytest.approx(stream.duration_ms - expected_trim_ms)


def test_playable_duration_ms_clamps_to_zero_when_trim_exceeds_duration():
    # A delay this large is impossible from a real LAME tag (its gapless
    # fields are 12-bit, max 4095 -- see _TWELVE_BIT_FIELD_LIMIT), but
    # AudioStream itself enforces no such range on these two fields (only
    # _parse_lame_gapless does, at read time) -- so trim_ms can exceed
    # duration_ms here, which is exactly the case max(0.0, ...) guards.
    stream = waxcut.load_audio_stream(FIXTURES / "cbr_stereo.mp3")
    huge_delay = 10_000_000
    trimmed = dataclasses.replace(stream, encoder_delay_samples=huge_delay, encoder_padding_samples=0)

    trim_ms = huge_delay / stream.sample_rate * 1000
    assert trim_ms > stream.duration_ms  # sanity check: this really does exceed duration_ms unclamped
    assert trimmed.playable_duration_ms == 0.0


REGRESSION_FIXTURES = Path(__file__).parent / "fixtures" / "regression"
_REGRESSION_FILES = sorted(REGRESSION_FIXTURES.glob("*.bin"))
# An empty glob here used to silently parametrize to zero cases, which
# pytest reports as a skip rather than a failure -- so a corpus that
# regressed to empty (e.g. a bad merge) would go unnoticed forever instead
# of failing the suite. Checking here, at collection time, turns that into
# a loud collection error instead. A plain `assert` would be stripped
# under Python's -O flag, silently reverting to that exact bug, so this
# raises explicitly instead.
if not _REGRESSION_FILES:
    raise RuntimeError(
        f"No .bin fixtures found under {REGRESSION_FIXTURES} -- the regression corpus must not be "
        "empty (see its README for what belongs here and why)."
    )

# A sentinel meaning "this call is expected to succeed, not raise" -- as
# opposed to a dict value of an actual exception type below.
_SUCCEEDS = object()

# Per-fixture expected outcome at each API layer, where known -- see
# tests/fixtures/regression/README.md for what each targets and why.
# scan_frames alone never touches Xing/VBR-header parsing (that only
# happens in load_audio_stream), so a fixture can legitimately succeed at
# the scan_frames layer and only raise once load_audio_stream inspects the
# VBR header frame -- that's why the two dicts differ.
_SCAN_FRAMES_EXPECTATIONS: dict[str, type[Exception] | object] = {
    "truncated_id3v2_tag.bin": waxcut.UnsupportedMp3Error,
    "truncated_vbr_flags_word.bin": _SUCCEEDS,
    "vbr_header_only_no_audio.bin": _SUCCEEDS,
    "frame_length_past_eof.bin": waxcut.UnsupportedMp3Error,
    "adversarial_0xff_no_sync.bin": waxcut.UnsupportedMp3Error,
}
_LOAD_AUDIO_STREAM_EXPECTATIONS: dict[str, type[Exception] | object] = dict.fromkeys(
    _SCAN_FRAMES_EXPECTATIONS, waxcut.UnsupportedMp3Error
)


def _assert_matches_expectation(call, expected):
    if expected is _SUCCEEDS:
        call()
    elif expected is None:
        # No specific claim for this fixture -- the smoke-test minimum:
        # parsing it must raise nothing but UnsupportedMp3Error (which
        # FileTooLargeError, also acceptable, is itself a subclass of).
        with contextlib.suppress(waxcut.UnsupportedMp3Error):
            call()
    else:
        with pytest.raises(expected):
            call()


@pytest.mark.parametrize("regression_file", _REGRESSION_FILES, ids=lambda p: p.name)
def test_regression_corpus_does_not_crash(regression_file, tmp_path):
    data = regression_file.read_bytes()
    _assert_matches_expectation(
        lambda: waxcut.scan_frames(data),
        _SCAN_FRAMES_EXPECTATIONS.get(regression_file.name),
    )

    # Also run every fixture through load_audio_stream, the same on-disk
    # entry point a real caller would use -- several fixtures here only
    # exercise their target code path there (VBR-header/gapless-tag
    # parsing), not in scan_frames alone.
    copied = tmp_path / regression_file.name
    copied.write_bytes(data)
    _assert_matches_expectation(
        lambda: waxcut.load_audio_stream(copied),
        _LOAD_AUDIO_STREAM_EXPECTATIONS.get(regression_file.name),
    )


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


def test_split_to_files_matches_split_at_segment_boundaries(fixture_path, tmp_path):
    stream = waxcut.load_audio_stream(fixture_path)
    duration = stream.playable_duration_ms
    timestamps = [duration * 0.25, duration * 0.6]
    expected = waxcut.split_at(stream, timestamps)

    output_paths = [tmp_path / f"segment{i}.mp3" for i in range(len(expected))]
    waxcut.split_to_files(stream, timestamps, output_paths)

    written = [path.read_bytes() for path in output_paths]
    assert written == expected
    assert waxcut.join_frames(written) == waxcut.join_frames(waxcut.split_at(stream, timestamps))


def test_split_to_files_rejects_mismatched_output_path_count(fixture_path, tmp_path):
    stream = waxcut.load_audio_stream(fixture_path)
    duration = stream.playable_duration_ms
    with pytest.raises(ValueError, match="output_paths"):
        waxcut.split_to_files(stream, [duration * 0.5], [tmp_path / "only_one.mp3"])


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


def test_load_audio_stream_accepts_a_str_path(fixture_path):
    # load_audio_stream called path.stat()/path.open() directly, so a str
    # argument raised a raw, undocumented AttributeError instead of the
    # FileNotFoundError/UnsupportedMp3Error/etc. contract the docstring
    # promises -- coercing via Path(path) makes str behave identically to
    # an already-constructed Path.
    from_str = waxcut.load_audio_stream(str(fixture_path))
    from_path = waxcut.load_audio_stream(fixture_path)
    assert list(from_str.frames) == list(from_path.frames)


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


def _mpeg1_stereo_128kbps_frame() -> bytes:
    """A complete (header + zero-filled body), valid MPEG1/128kbps/44100Hz/stereo frame."""
    header = _mpeg1_stereo_128kbps_header(protection_bit=1)
    frame_length = 417  # 144 * 128000 / 44100, truncated, no padding
    return header + b"\x00" * (frame_length - len(header))


def test_scan_frames_rejects_a_spurious_sync_before_real_audio(tmp_path):
    # C3 regression: scan_frames accepted the very first candidate frame
    # the instant its own 4 header bytes validated, without confirming a
    # second real sync followed it -- a syntactically valid 4-byte header
    # occurs by chance in arbitrary binary every few hundred KB and can be
    # planted deliberately. A crafted MPEG2.5/8kbps/8000Hz header (72-byte
    # claimed frame length) prepended to real MPEG1 audio made the whole
    # file's reported sample_rate wrong by 5.5x and silently dropped the
    # real first frame (its bytes fell inside the fake frame's claimed
    # span). The first frame must now be confirmed by a second real sync
    # immediately after it (or EOF) before being trusted.
    fake_header = bytes.fromhex("ffe31400")
    real_audio = _mpeg1_stereo_128kbps_frame() * 5

    crafted = tmp_path / "crafted.mp3"
    crafted.write_bytes(fake_header + real_audio)
    honest = tmp_path / "honest.mp3"
    honest.write_bytes(real_audio)

    crafted_stream = waxcut.load_audio_stream(crafted)
    honest_stream = waxcut.load_audio_stream(honest)

    assert crafted_stream.sample_rate == honest_stream.sample_rate == 44100
    assert len(crafted_stream.frames) == len(honest_stream.frames) == 5
    crafted_whole = waxcut.slice_bytes(crafted_stream.data, crafted_stream.frames, 0, 5)
    honest_whole = waxcut.slice_bytes(honest_stream.data, honest_stream.frames, 0, 5)
    assert crafted_whole == honest_whole

    raw = waxcut.scan_frames(crafted.read_bytes())
    assert raw[0].offset == len(fake_header)  # locked onto the real frame, not the fake one


def test_scan_frames_still_accepts_a_genuine_single_frame_with_a_trailer(tmp_path):
    # Companion to the above: the confirmation check must not reject a
    # genuine single-frame file just because nothing after it looks like a
    # second MPEG header -- a real single-frame MP3 followed only by a
    # short, non-frame trailer (an ID3v1 tag, in this construction) looks
    # exactly like an unconfirmable first frame too. Must still recover
    # the one real frame rather than reporting no audio found at all.
    single_frame = _mpeg1_stereo_128kbps_frame()
    id3v1_trailer = b"TAG" + b"\x00" * 125

    synthetic_mp3 = tmp_path / "single_plus_trailer.mp3"
    synthetic_mp3.write_bytes(single_frame + id3v1_trailer)

    stream = waxcut.load_audio_stream(synthetic_mp3)
    assert len(stream.frames) == 1
    assert stream.sample_rate == 44100
