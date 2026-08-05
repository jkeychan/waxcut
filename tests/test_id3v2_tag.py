"""Byte-level correctness and real-world-reader cross-validation for
write_id3v2_tag. See test_frames.py for id3v2_size (the read-side
companion) and test_misuse.py for input-validation cases."""

import io
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


@pytest.mark.parametrize(
    "fixture_name",
    [
        "cbr_stereo.mp3",
        "cbr_mono.mp3",
        pytest.param("vbr_stereo.mp3", marks=pytest.mark.xfail(reason=_VBR_XFAIL_REASON, strict=True)),
        pytest.param("lame_vbr_stereo.mp3", marks=pytest.mark.xfail(reason=_VBR_XFAIL_REASON, strict=True)),
    ],
)
def test_tagged_split_output_is_a_valid_mp3_with_correct_duration(fixture_name, tmp_path):
    # End-to-end: load -> slice_bytes (untagged, per its own contract) ->
    # write_id3v2_tag -> written to disk -> opened by mutagen.mp3.MP3 as a
    # complete file, confirming the ID3v2 tag doesn't perturb frame
    # scanning/duration for a real-world reader, and that the tag itself
    # is attached and legible via MP3.tags.
    stream = waxcut.load_audio_stream(FIXTURES / fixture_name)
    whole = waxcut.slice_bytes(stream.data, stream.frames, 0, len(stream.frames))
    assert whole[:3] != b"ID3"  # confirms slice_bytes output really has no tag of its own

    tagged = waxcut.write_id3v2_tag(whole, title="Track One", artist="Test Artist", track=1)
    out_path = tmp_path / "tagged.mp3"
    out_path.write_bytes(tagged)

    parsed = MP3(out_path)
    assert parsed.tags["TIT2"].text == ["Track One"]
    assert parsed.tags["TRCK"].text == ["1"]
    assert parsed.info.length * 1000 == pytest.approx(stream.playable_duration_ms, abs=1.0)
