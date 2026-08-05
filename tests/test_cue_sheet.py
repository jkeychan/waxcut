"""Tests for waxcut.parse_cue_sheet: CUE-sheet text -> cut-point timestamps."""

import pytest

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
    ],
)
def test_parse_msf_rejects_malformed_or_out_of_range(token):
    if token == "0:0:0":  # noqa: S105
        assert _parse_msf(token, lineno=1) == 0.0
        return
    with pytest.raises(CueSheetError, match=r"line 1"):
        _parse_msf(token, lineno=1)
