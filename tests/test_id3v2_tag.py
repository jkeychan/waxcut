"""Byte-level correctness and real-world-reader cross-validation for
write_id3v2_tag. See test_frames.py for id3v2_size (the read-side
companion) and test_misuse.py for input-validation cases."""

import waxcut


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
