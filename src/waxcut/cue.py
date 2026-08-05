"""Parsing for CUE sheet (.cue) text -- pure text, no audio decoding.

Extracts TRACK/INDEX 01 cut-point timestamps for feeding directly into
waxcut.split_at. See docs/superpowers/plans/2026-08-04-cue-sheet-parsing-plan.md
for the full design rationale (grammar scope, error-handling rules, worked
examples) -- kept out of this docstring to avoid duplicating it.
"""

from __future__ import annotations

_CD_FRAMES_PER_SECOND = 75
_SECONDS_PER_MINUTE = 60
_MS_PER_SECOND = 1000
_MSF_FIELDS = 3  # MM:SS:FF has exactly 3 fields
_MAX_SECONDS_FIELD = 60  # exclusive upper bound: valid range is 0-59
_MAX_FRAME_FIELD = _CD_FRAMES_PER_SECOND  # exclusive upper bound: valid range is 0-74


class CueSheetError(ValueError):
    """Raised when cue-sheet text can't be parsed into cut-point timestamps.

    Covers malformed MM:SS:FF timestamps, a TRACK with no INDEX 01, cue
    text with no audio tracks at all, out-of-order INDEX 01 timestamps,
    and multi-FILE cue sheets (unsupported -- see the parse_cue_sheet
    docstring). Always raised with a message naming the offending line.
    """


def _parse_msf(token: str, lineno: int) -> float:
    """Convert a CD MM:SS:FF timecode token to milliseconds.

    MM:SS:FF is Red Book CD timecode: minutes, seconds (0-59), and CD
    "frames" (0-74, at 75 frames/second) -- unrelated to an MPEG audio
    Frame elsewhere in this codebase; it's a fixed 1/75-second CD unit.

    Args:
        token: The raw MM:SS:FF field, e.g. "03:25:37".
        lineno: 1-based source line number, for the error message only.

    Returns:
        The equivalent duration in milliseconds.

    Raises:
        CueSheetError: `token` isn't exactly 3 colon-separated fields,
            any field isn't a base-10 integer, seconds is outside 0-59,
            or the frame field is outside 0-74.
    """
    fields = token.split(":")
    if len(fields) != _MSF_FIELDS:
        raise CueSheetError(f"line {lineno}: malformed timestamp {token!r} (expected MM:SS:FF)")
    try:
        minutes, seconds, frames = (int(field) for field in fields)
    except ValueError:
        raise CueSheetError(
            f"line {lineno}: malformed timestamp {token!r} (expected MM:SS:FF, all fields numeric)"
        ) from None
    if not (0 <= seconds < _MAX_SECONDS_FIELD):
        raise CueSheetError(f"line {lineno}: {token!r} out of range (seconds must be 0-59)")
    if not (0 <= frames < _MAX_FRAME_FIELD):
        raise CueSheetError(
            f"line {lineno}: {token!r} out of range (frame field must be 0-74 at 75 frames/sec)"
        )
    if minutes < 0:
        raise CueSheetError(f"line {lineno}: {token!r} has a negative minutes field")
    total_seconds = minutes * _SECONDS_PER_MINUTE + seconds
    return total_seconds * _MS_PER_SECOND + frames * _MS_PER_SECOND / _CD_FRAMES_PER_SECOND
