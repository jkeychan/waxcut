"""Byte-level correctness and real-world-reader cross-validation for
write_id3v2_tag. See test_frames.py for id3v2_size (the read-side
companion) and test_misuse.py for input-validation cases."""

import io
import struct
from pathlib import Path

import pytest
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

import waxcut

FIXTURES = Path(__file__).parent / "fixtures"


def test_write_id3v2_tag_produces_exact_expected_bytes():
    tagged = waxcut.write_id3v2_tag(b"REST OF FILE", title="A", artist="B", track=1)
    expected_header = (
        b"ID3\x03\x00\x00"  # "ID3", version 2.3.0, flags
        b"\x00\x00\x00\x24"  # syncsafe size = 36 (3 frames x 12 bytes)
    )
    expected_frames = (
        b"TIT2\x00\x00\x00\x02\x00\x00\x00ATPE1\x00\x00\x00\x02\x00\x00\x00BTRCK\x00\x00\x00\x02\x00\x00\x001"
    )
    assert tagged == expected_header + expected_frames + b"REST OF FILE"
    assert len(tagged) == 10 + 36 + len(b"REST OF FILE")


def test_frame_size_field_is_plain_big_endian_not_syncsafe_above_127_bytes():
    # Every other test in this file uses short text, so its frame content
    # stays under 128 bytes -- syncsafe and plain big-endian encode
    # identically below that threshold (both are 0x00 0x00 0x00 <7-bit
    # value> for a one-byte quantity), so no existing test can tell them
    # apart. A 199-char Latin-1 title makes the TIT2 frame's content exactly
    # 200 bytes (1 encoding-flag byte + 199 text bytes), which is where the
    # two encodings first diverge: struct.pack(">I", 200) is
    # b"\x00\x00\x00\xc8", but ID3v2's syncsafe encoding of 200 (7
    # significant bits/byte, v2.4-only -- this tag is v2.3) is
    # b"\x00\x00\x01\x48" instead, since 200 doesn't fit in 7 bits.
    title = "A" * 199
    tagged = waxcut.write_id3v2_tag(b"REST", title=title)

    # Latin-1 content is the 1-byte encoding flag plus the text verbatim
    # (one byte per char for plain ASCII like this) -- 1 + 199 = 200.
    content_length = 1 + len(title)
    assert content_length == 200

    frame_id = tagged[10:14]
    frame_size_field = tagged[14:18]
    assert frame_id == b"TIT2"
    assert frame_size_field == struct.pack(">I", 200) == b"\x00\x00\x00\xc8"
    assert frame_size_field != b"\x00\x00\x01\x48"  # what syncsafe(200) would have produced instead


def test_write_id3v2_tag_with_no_fields_is_a_no_op_prepend_of_an_empty_tag():
    # All three fields omitted is *not* rejected -- it is a legitimate,
    # if unusual, request for a minimal empty ID3v2 tag (spec-legal: a
    # tag with zero frames is valid). See test_misuse.py for the case
    # this plan *does* reject.
    tagged = waxcut.write_id3v2_tag(b"DATA")
    assert tagged == b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"DATA"


def test_write_id3v2_tag_single_field_only_writes_that_frame():
    tagged = waxcut.write_id3v2_tag(b"X", title="Solo")
    assert b"TPE1" not in tagged
    assert b"TRCK" not in tagged
    assert b"TIT2" in tagged


def test_write_id3v2_tag_round_trips_through_mutagen():
    tagged = waxcut.write_id3v2_tag(b"\xff\xfb\x90\x00", title="Track One", artist="Test Artist", track=3)
    parsed = ID3(fileobj=io.BytesIO(tagged))
    assert parsed["TIT2"].text == ["Track One"]
    assert parsed["TPE1"].text == ["Test Artist"]
    assert parsed["TRCK"].text == ["3"]


def test_write_id3v2_tag_unicode_title_round_trips_as_utf16():
    # Confirms the UTF-16 fallback path (Design Decision 4) is not just
    # self-consistent but genuinely readable -- and that a Latin-1-only
    # field (artist) and a UTF-16 field (title) coexist correctly in one
    # tag, each with its own correct per-frame encoding byte.
    tagged = waxcut.write_id3v2_tag(b"\xff\xfb\x90\x00", title="日本語タイトル", artist="Bjork", track=1)
    parsed = ID3(fileobj=io.BytesIO(tagged))
    assert parsed["TIT2"].text == ["日本語タイトル"]
    assert parsed["TPE1"].text == ["Bjork"]


_VBR_XFAIL_REASON = (
    "AudioStream.frames deliberately excludes the Xing/VBRI VBR header frame "
    "(encoder metadata, not audio -- see AudioStream's docstring), so "
    "slice_bytes output for a genuinely variable-bitrate file has no VBR "
    "header. mutagen.mp3.MPEGInfo falls back to estimating duration from the "
    "first frame's bitrate alone when no Xing/VBRI header is present, which "
    "is accurate for CBR (see the two passing cases below) but wildly wrong "
    "for true VBR audio -- confirmed by cross-checking with ffprobe, which "
    "decodes the actual frames rather than trusting a header and reports a "
    "duration far closer to stream.playable_duration_ms than mutagen's "
    "fallback does. The tag content itself (TIT2/TRCK) round-trips correctly "
    "regardless -- only mutagen's derived duration is unreliable here, and "
    "reconstructing a synthetic VBR header per split is out of scope for v1."
)


_ALL_FIXTURES = ["cbr_stereo.mp3", "cbr_mono.mp3", "vbr_stereo.mp3", "lame_vbr_stereo.mp3"]


def _load_tagged(fixture_name, tmp_path):
    """load -> slice_bytes (untagged, per its own contract) -> write_id3v2_tag -> written to disk.

    Shared by the tag-content and duration tests below so both exercise the
    same end-to-end path without duplicating it.
    """
    stream = waxcut.load_audio_stream(FIXTURES / fixture_name)
    whole = waxcut.slice_bytes(stream.data, stream.frames, 0, len(stream.frames))
    assert whole[:3] != b"ID3"  # confirms slice_bytes output really has no tag of its own

    tagged = waxcut.write_id3v2_tag(whole, title="Track One", artist="Test Artist", track=1)
    out_path = tmp_path / "tagged.mp3"
    out_path.write_bytes(tagged)
    return stream, MP3(out_path)


@pytest.mark.parametrize("fixture_name", _ALL_FIXTURES)
def test_tagged_split_output_has_correct_tag_content(fixture_name, tmp_path):
    # Confirms the ID3v2 tag doesn't perturb frame scanning for a
    # real-world reader, and that the tag itself is attached and legible
    # via MP3.tags -- unconditionally, for all four fixtures including the
    # two VBR ones, so a tag-content regression can't be masked by the
    # duration xfail below (see test_tagged_split_output_has_correct_duration).
    _, parsed = _load_tagged(fixture_name, tmp_path)
    assert parsed.tags["TIT2"].text == ["Track One"]
    assert parsed.tags["TRCK"].text == ["1"]


@pytest.mark.parametrize(
    "fixture_name",
    [
        "cbr_stereo.mp3",
        "cbr_mono.mp3",
        pytest.param("vbr_stereo.mp3", marks=pytest.mark.xfail(reason=_VBR_XFAIL_REASON, strict=True)),
        pytest.param("lame_vbr_stereo.mp3", marks=pytest.mark.xfail(reason=_VBR_XFAIL_REASON, strict=True)),
    ],
)
def test_tagged_split_output_has_correct_duration(fixture_name, tmp_path):
    # Isolated from tag-content assertions on purpose: this is the only
    # property that's unreliable for true VBR fixtures (see
    # _VBR_XFAIL_REASON), so it's the only assertion scoped by xfail.
    stream, parsed = _load_tagged(fixture_name, tmp_path)
    assert parsed.info.length * 1000 == pytest.approx(stream.playable_duration_ms, abs=1.0)
