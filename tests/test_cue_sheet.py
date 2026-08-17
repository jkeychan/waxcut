"""Tests for waxcut.parse_cue_sheet: CUE-sheet text -> cut-point timestamps."""

from pathlib import Path

import pytest

import waxcut
from waxcut.cue import CueSheetError, _parse_msf
from waxcut.frames import WaxcutError


def test_cue_sheet_error_is_a_waxcut_error():
    assert issubclass(CueSheetError, WaxcutError)


@pytest.mark.parametrize(
    ("token", "expected_ms"),
    [
        ("00:00:00", 0.0),
        ("03:25:37", 205493.33333333334),
        ("03:22:18", 202240.0),
        ("07:00:00", 420000.0),
        ("00:00:74", pytest.approx(986.666, abs=0.001)),  # max valid frame value
        ("99:59:74", pytest.approx(5999986.666, abs=0.001)),  # no upper bound on minutes
    ],
)
def test_parse_msf_converts_correctly(token, expected_ms):
    assert _parse_msf(token, lineno=1) == pytest.approx(expected_ms, abs=0.001)


@pytest.mark.parametrize(
    "token",
    [
        "0:0:0",  # not zero-padded -- still valid, just confirming leniency
        "00:00",  # only 2 fields
        "00:00:00:00",  # 4 fields
        "aa:bb:cc",  # non-numeric
        "00:60:00",  # seconds out of range (0-59)
        "00:00:75",  # frame out of range (0-74)
        "",  # empty
        "-1:00:00",  # negative minutes
        "-1:99:00",  # negative minutes, invalid seconds too -- minutes must be reported first
        "9999999:00:00",  # minutes over _MAX_MINUTES_FIELD
    ],
)
def test_parse_msf_rejects_malformed_or_out_of_range(token):
    if token == "0:0:0":  # noqa: S105
        assert _parse_msf(token, lineno=1) == 0.0
        return
    with pytest.raises(CueSheetError, match=r"line 1"):
        _parse_msf(token, lineno=1)


def test_parse_msf_rejects_negative_minutes_with_specific_message():
    with pytest.raises(CueSheetError, match="invalid minutes field"):
        _parse_msf("-1:00:00", lineno=1)


def test_parse_msf_rejects_oversized_minutes_field_not_overflowerror():
    # Reproduces the reviewer's crash case: a few-hundred-digit minutes
    # field used to blow past int-to-float conversion range and raise a
    # raw OverflowError instead of CueSheetError.
    token = "9" * 400 + ":00:00"
    with pytest.raises(CueSheetError, match="invalid minutes field"):
        _parse_msf(token, lineno=1)


def test_parse_msf_rejects_field_over_int_string_conversion_limit_with_honest_message():
    # A minutes field longer than sys.get_int_max_str_digits() (default
    # 4300, since Python 3.11) makes int() itself raise ValueError with its
    # own "Exceeds the limit... for integer string conversion" message --
    # distinct from, and longer than, the 400-digit case above (which stays
    # under the limit and reaches the normal range check). That ValueError
    # used to be caught by the generic except ValueError and reported as
    # "all fields numeric", which is misleading: the field is numeric, just
    # too long.
    token = "9" * 5000 + ":00:00"
    with pytest.raises(CueSheetError, match="exceeds") as exc_info:
        _parse_msf(token, lineno=1)
    assert "all fields numeric" not in str(exc_info.value)


@pytest.mark.parametrize(
    "token",
    [
        "0_1:00:00",  # PEP 515 underscore separator
        "+1:00:00",  # leading plus sign
        "٠١:٠٠:٠٠",  # Arabic-Indic digits -- int() accepts these too  # noqa: RUF001
    ],
)
def test_parse_msf_rejects_int_leniency_int_would_silently_accept(token):
    # int() is more permissive than the "base-10 integer" docstring implies;
    # confirm none of these slip through as a valid timestamp.
    with pytest.raises(CueSheetError, match="all fields numeric"):
        _parse_msf(token, lineno=1)


MINIMAL_CUE = """\
REM GENRE Reggae
REM DATE 1978
PERFORMER "The Wailers"
TITLE "Kaya"
FILE "kaya.mp3" MP3
  TRACK 01 AUDIO
    TITLE "Easy Skanking"
    PERFORMER "The Wailers"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "Kaya"
    PERFORMER "The Wailers"
    INDEX 00 03:22:18
    INDEX 01 03:25:37
  TRACK 03 AUDIO
    TITLE "Sun Is Shining"
    PERFORMER "The Wailers"
    INDEX 01 07:00:00
"""


def test_parse_cue_sheet_worked_example():
    result = waxcut.parse_cue_sheet(MINIMAL_CUE)
    assert result == pytest.approx([205493.33333333334, 420000.0])


def test_parse_cue_sheet_drops_leading_zero_cut_point():
    # Track 1's INDEX 01 00:00:00 must not appear in the output -- see
    # design decision #6 in the plan: it would produce a spurious empty
    # first segment when fed into split_at.
    result = waxcut.parse_cue_sheet(MINIMAL_CUE)
    assert 0.0 not in result


def test_parse_cue_sheet_keeps_nonzero_first_index():
    text = 'FILE "x.mp3" MP3\n  TRACK 01 AUDIO\n    INDEX 01 00:05:00\n'
    result = waxcut.parse_cue_sheet(text)
    assert result == pytest.approx([5000.0])


def test_parse_cue_sheet_single_track_returns_empty_list():
    text = 'FILE "x.mp3" MP3\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
    assert waxcut.parse_cue_sheet(text) == []


def test_parse_cue_sheet_ignores_pregap_postgap_and_index_00():
    text = (
        'FILE "x.mp3" MP3\n'
        "  TRACK 01 AUDIO\n"
        "    INDEX 01 00:00:00\n"
        "  TRACK 02 AUDIO\n"
        "    PREGAP 00:02:00\n"
        "    INDEX 00 01:58:00\n"
        "    INDEX 01 02:00:00\n"
        "    POSTGAP 00:01:00\n"
    )
    assert waxcut.parse_cue_sheet(text) == pytest.approx([120000.0])


def test_parse_cue_sheet_ignores_non_audio_tracks():
    # Track 1's INDEX 01 is deliberately nonzero (00:00:02) rather than
    # 00:00:00: if the AUDIO/non-AUDIO type check ever broke and let this
    # MODE1/2352 track through, its timestamp would survive the leading-
    # zero-drop rule and show up in the result -- a zero timestamp would
    # get silently dropped by that rule either way, masking the failure.
    text = (
        'FILE "x.bin" BINARY\n'
        "  TRACK 01 MODE1/2352\n"
        "    INDEX 01 00:00:02\n"
        "  TRACK 02 AUDIO\n"
        "    INDEX 01 00:05:00\n"
    )
    assert waxcut.parse_cue_sheet(text) == pytest.approx([5000.0])


def test_parse_cue_sheet_keeps_track_with_trailing_comment_token():
    # TRACK type lives at a fixed grammar position (TRACK <number> <type>),
    # not at the end of the line -- a trailing comment or stray token after
    # AUDIO must not cause the track to be misread as non-audio and dropped.
    text = (
        'FILE "x.mp3" MP3\n'
        "  TRACK 01 AUDIO\n"
        "    INDEX 01 00:00:00\n"
        "  TRACK 02 AUDIO  ; ripped with X\n"
        "    INDEX 01 00:05:00\n"
    )
    assert waxcut.parse_cue_sheet(text) == pytest.approx([5000.0])


def test_parse_cue_sheet_rejects_data_track_with_trailing_audio_token():
    # The converse: a data track type with an extra trailing token that
    # happens to be "AUDIO" must not be misread as an audio track just
    # because AUDIO is the last whitespace-split token on the line.
    text = (
        'FILE "x.bin" BINARY\n'
        "  TRACK 01 MODE1/2352 AUDIO\n"
        "    INDEX 01 00:00:02\n"
        "  TRACK 02 AUDIO\n"
        "    INDEX 01 00:05:00\n"
    )
    assert waxcut.parse_cue_sheet(text) == pytest.approx([5000.0])


def test_parse_cue_sheet_ignores_track_line_missing_type_field():
    # A TRACK line with no type token at all (fewer than the 3 fields
    # TRACK <number> <type> requires) must be treated like any other
    # non-AUDIO track -- skipped, not a crash. Nothing about the type
    # position guarantees a type is present; _TRACK_LINE_MIN_FIELDS is the
    # only thing standing between this and an IndexError on parts[2].
    text = 'FILE "x.bin" BINARY\n  TRACK 01\n    INDEX 01 00:00:02\n  TRACK 02 AUDIO\n    INDEX 01 00:05:00\n'
    assert waxcut.parse_cue_sheet(text) == pytest.approx([5000.0])


def test_parse_cue_sheet_rejects_no_tracks_found():
    with pytest.raises(waxcut.CueSheetError, match="no audio TRACK/INDEX 01"):
        waxcut.parse_cue_sheet("this is not a cue sheet at all\njust some text\n")


def test_parse_cue_sheet_rejects_empty_text():
    with pytest.raises(waxcut.CueSheetError):
        waxcut.parse_cue_sheet("")


def test_parse_cue_sheet_rejects_multi_file():
    text = (
        'FILE "disc1.mp3" MP3\n'
        "  TRACK 01 AUDIO\n"
        "    INDEX 01 00:00:00\n"
        'FILE "disc2.mp3" MP3\n'
        "  TRACK 02 AUDIO\n"
        "    INDEX 01 00:00:00\n"
    )
    with pytest.raises(waxcut.CueSheetError, match="multi-FILE"):
        waxcut.parse_cue_sheet(text)


def test_parse_cue_sheet_rejects_track_missing_index_01():
    text = 'FILE "x.mp3" MP3\n  TRACK 01 AUDIO\n    INDEX 00 00:00:00\n'
    with pytest.raises(waxcut.CueSheetError, match="no INDEX 01"):
        waxcut.parse_cue_sheet(text)


def test_parse_cue_sheet_missing_index_01_error_names_track_line_not_closing_line():
    # The broken track is TRACK 02 on line 4 -- no INDEX 01 before TRACK 03
    # opens on line 8. The error must point at line 4 (where TRACK 02 was
    # declared), not line 8 (where its block happens to end).
    text = (
        'FILE "x.mp3" MP3\n'
        "TRACK 01 AUDIO\n"
        "INDEX 01 00:00:00\n"
        "TRACK 02 AUDIO\n"
        "REM x\n"
        "REM y\n"
        "REM z\n"
        "TRACK 03 AUDIO\n"
        "INDEX 01 00:05:00\n"
    )
    with pytest.raises(waxcut.CueSheetError, match=r"^line 4: TRACK 02 has no INDEX 01$"):
        waxcut.parse_cue_sheet(text)


def test_parse_cue_sheet_no_audio_tracks_found_has_no_line_number():
    # No single line is "the" offending one when nothing was found at all --
    # see the CueSheetError docstring's explicit carve-out for this case.
    with pytest.raises(waxcut.CueSheetError, match="no audio TRACK/INDEX 01") as exc_info:
        waxcut.parse_cue_sheet("this is not a cue sheet at all\njust some text\n")
    assert "line " not in str(exc_info.value)


def test_parse_cue_sheet_rejects_oversized_minutes_field():
    # Full-pipeline version of the reviewer's crash reproduction: this used
    # to raise an uncaught OverflowError instead of CueSheetError.
    text = 'FILE "x" MP3\n TRACK 01 AUDIO\n  INDEX 01 ' + "9" * 400 + ":00:00\n"
    with pytest.raises(waxcut.CueSheetError, match="invalid minutes field"):
        waxcut.parse_cue_sheet(text)


def test_parse_cue_sheet_rejects_duplicate_index_01_within_one_track():
    # Two INDEX 01 lines under one TRACK used to silently append two
    # timestamps for it instead of being flagged as the malformed input
    # it is.
    text = (
        'FILE "x.mp3" MP3\n'
        "  TRACK 01 AUDIO\n"
        "    INDEX 01 00:00:00\n"
        "  TRACK 02 AUDIO\n"
        "    INDEX 01 00:05:00\n"
        "    INDEX 01 00:07:00\n"
    )
    with pytest.raises(waxcut.CueSheetError, match="more than one INDEX 01"):
        waxcut.parse_cue_sheet(text)


def test_parse_cue_sheet_rejects_out_of_order_timestamps():
    text = (
        'FILE "x.mp3" MP3\n  TRACK 01 AUDIO\n    INDEX 01 00:05:00\n  TRACK 02 AUDIO\n    INDEX 01 00:03:00\n'
    )
    with pytest.raises(waxcut.CueSheetError, match="not increasing"):
        waxcut.parse_cue_sheet(text)


def test_parse_cue_sheet_allows_duplicate_consecutive_timestamps():
    # Matches split_at's own documented behavior: a duplicate timestamp
    # produces an empty segment rather than being treated as an error.
    text = (
        'FILE "x.mp3" MP3\n'
        "  TRACK 01 AUDIO\n"
        "    INDEX 01 00:00:00\n"
        "  TRACK 02 AUDIO\n"
        "    INDEX 01 00:05:00\n"
        "  TRACK 03 AUDIO\n"
        "    INDEX 01 00:05:00\n"
    )
    assert waxcut.parse_cue_sheet(text) == pytest.approx([5000.0, 5000.0])


def test_parse_cue_sheet_is_case_insensitive_on_keywords():
    text = 'file "x.mp3" mp3\n  track 01 audio\n    index 01 00:05:00\n'
    assert waxcut.parse_cue_sheet(text) == pytest.approx([5000.0])


def test_parse_cue_sheet_recognizes_tab_delimited_keywords():
    # The outer keyword dispatch used to prefix-match a literal space after
    # TRACK/FILE/INDEX, so a tab-delimited line like "TRACK\t01\tAUDIO" never
    # reached the TRACK-handling branch at all -- silently falling through
    # as an unrecognized line rather than an error.
    text = 'FILE\t"x.mp3"\tMP3\n\tTRACK\t01\tAUDIO\n\t\tINDEX\t01\t00:05:00\n'
    assert waxcut.parse_cue_sheet(text) == pytest.approx([5000.0])


def test_parse_cue_sheet_recognizes_nbsp_delimited_keywords():
    # Same class of bug as the tab case, via a non-breaking space (U+00A0)
    # after the keyword instead of a regular space.
    text = 'FILE\xa0"x.mp3"\xa0MP3\n  TRACK\xa001\xa0AUDIO\n    INDEX\xa001\xa000:05:00\n'
    assert waxcut.parse_cue_sheet(text) == pytest.approx([5000.0])


def test_parse_cue_sheet_strips_leading_bom():
    # A caller decoding with "utf-8" instead of "utf-8-sig" retains a
    # leading BOM -- common with real-world .cue files from EAC and other
    # rippers. A BOM'd leading TRACK line used to fail the keyword match
    # entirely: the line was silently treated as unrecognized, so track 1
    # never entered in_audio_track state and its (deliberately nonzero, to
    # rule out the leading-zero-drop rule) INDEX 01 silently vanished from
    # the output instead of appearing before track 2's.
    text = "﻿TRACK 01 AUDIO\n  INDEX 01 00:02:00\nTRACK 02 AUDIO\n  INDEX 01 00:05:00\n"
    assert waxcut.parse_cue_sheet(text) == pytest.approx([2000.0, 5000.0])


def test_parse_cue_sheet_strips_a_double_bom():
    # N12 regression: a real artifact of tools that decode with "utf-8"
    # (leaving one BOM) and then re-encode with "utf-8-sig" (adding a
    # second) is a double-BOM file. A single removeprefix() call left one
    # BOM behind -- still enough to defeat the FILE/TRACK keyword match,
    # just as effectively as before the fix. Found by a fresh adversarial
    # code review.
    text = "﻿﻿TRACK 01 AUDIO\n  INDEX 01 00:02:00\nTRACK 02 AUDIO\n  INDEX 01 00:05:00\n"
    assert waxcut.parse_cue_sheet(text) == pytest.approx([2000.0, 5000.0])


def test_parse_cue_sheet_recognizes_unpadded_index_number():
    # N13 regression: real-world cue sheets from tools like cdrdao (and
    # hand-written ones) sometimes emit "INDEX 1" rather than "INDEX 01".
    # An exact string match against "01" silently dropped the cut point
    # instead of recognizing it -- no error, just a missing track split.
    # Found by a fresh adversarial code review.
    text = 'FILE "x.mp3" MP3\n  TRACK 01 AUDIO\n    INDEX 1 00:05:00\n'
    assert waxcut.parse_cue_sheet(text) == pytest.approx([5000.0])


def test_parse_cue_sheet_error_messages_truncate_long_tokens():
    # N1 regression: error messages interpolated the raw, attacker-
    # controlled token verbatim and unbounded -- a pathologically long
    # malformed field produced a message just as long. Found by a fresh
    # adversarial code review.
    huge_token = "9" * 10_000
    text = f'FILE "x.mp3" MP3\n  TRACK 01 AUDIO\n    INDEX 01 {huge_token}\n'
    with pytest.raises(waxcut.CueSheetError) as exc_info:
        waxcut.parse_cue_sheet(text)
    assert len(str(exc_info.value)) < 500


def test_parse_cue_sheet_does_not_split_on_non_newline_unicode_line_separators():
    # N15 regression: splitlines() also breaks on a wider set of Unicode
    # line-separator characters (here, \x0b, vertical tab) than just "\n".
    # Pre-fix, "INDEX\x0b01 00:05:00" split into two lines ("INDEX" and
    # "01 00:05:00"): neither has "INDEX" as its own keyword with enough
    # fields, so the INDEX 01 was never recognized and the track was
    # rejected as having no INDEX 01 at all -- a whitespace character
    # inside one line silently broke parsing of a line that, tokenized by
    # str.split() alone (which does treat \x0b as a separator, same as
    # any other whitespace), is perfectly well-formed. Found by a fresh
    # adversarial code review.
    text = 'FILE "x.mp3" MP3\n  TRACK 01 AUDIO\n    INDEX\x0b01 00:05:00\n'
    assert waxcut.parse_cue_sheet(text) == pytest.approx([5000.0])


def test_parse_cue_sheet_output_feeds_split_at():
    # Integration smoke test: the real thing this feature exists for.
    # Uses an existing MP3 fixture with cut points that comfortably fit
    # inside its duration; see tests/fixtures for available files.
    fixtures = Path(__file__).parent / "fixtures"
    stream = waxcut.load_audio_stream(fixtures / "cbr_stereo.mp3")
    text = (
        'FILE "x.mp3" MP3\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    INDEX 01 00:00:37\n'
    )
    # Simpler and less brittle than hand-encoding a real MSF split point:
    # just prove parse_cue_sheet's *output shape* is exactly what split_at
    # accepts, using a fixed, comfortably-in-range synthetic timestamp.
    timestamps = waxcut.parse_cue_sheet(text)
    segments = waxcut.split_at(stream, timestamps)
    assert len(segments) == len(timestamps) + 1
    assert waxcut.join_frames(segments) == waxcut.slice_bytes(
        stream.data, stream.frames, 0, len(stream.frames)
    )
