"""Tests for waxcut.parse_cue_sheet: CUE-sheet text -> cut-point timestamps."""

from pathlib import Path

import pytest

import waxcut
from waxcut.cue import CueSheetError, _parse_msf


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
    text = (
        'FILE "x.bin" BINARY\n'
        "  TRACK 01 MODE1/2352\n"
        "    INDEX 01 00:00:00\n"
        "  TRACK 02 AUDIO\n"
        "    INDEX 01 00:05:00\n"
    )
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


def test_parse_cue_sheet_rejects_oversized_minutes_field():
    # Full-pipeline version of the reviewer's crash reproduction: this used
    # to raise an uncaught OverflowError instead of CueSheetError.
    text = 'FILE "x" MP3\n TRACK 01 AUDIO\n  INDEX 01 ' + "9" * 400 + ":00:00\n"
    with pytest.raises(waxcut.CueSheetError, match="invalid minutes field"):
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
