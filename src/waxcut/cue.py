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
_MAX_MINUTES_FIELD = 1_000_000  # inclusive upper bound: keeps the ms conversion within float range


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
            any field isn't a base-10 integer, minutes is negative or
            exceeds `_MAX_MINUTES_FIELD`, seconds is outside 0-59, or
            the frame field is outside 0-74.
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
    if not (0 <= minutes <= _MAX_MINUTES_FIELD):
        raise CueSheetError(
            f"line {lineno}: {token!r} has an invalid minutes field (must be 0-{_MAX_MINUTES_FIELD})"
        )
    if not (0 <= seconds < _MAX_SECONDS_FIELD):
        raise CueSheetError(f"line {lineno}: {token!r} out of range (seconds must be 0-59)")
    if not (0 <= frames < _MAX_FRAME_FIELD):
        raise CueSheetError(
            f"line {lineno}: {token!r} out of range (frame field must be 0-74 at 75 frames/sec)"
        )
    total_seconds = minutes * _SECONDS_PER_MINUTE + seconds
    return total_seconds * _MS_PER_SECOND + frames * _MS_PER_SECOND / _CD_FRAMES_PER_SECOND


_AUDIO_TRACK_TYPE = "AUDIO"
_TARGET_INDEX_NUMBER = "01"
_TRACK_LINE_MIN_FIELDS = 3  # TRACK <number> <type>
_INDEX_LINE_MIN_FIELDS = 3  # INDEX <number> <MM:SS:FF>


def _handle_track_line(line: str) -> tuple[str, bool]:
    """Parse a TRACK line; return (track_number, is_audio_track)."""
    parts = line.split()
    track_number = parts[1] if len(parts) > 1 else "?"
    is_audio = parts[-1].upper() == _AUDIO_TRACK_TYPE if len(parts) >= _TRACK_LINE_MIN_FIELDS else False
    return track_number, is_audio


def _handle_index_line(line: str, lineno: int) -> float | None:
    """Parse an INDEX line; return its ms value if it's INDEX 01, else None."""
    parts = line.split()
    if len(parts) < _INDEX_LINE_MIN_FIELDS or parts[1] != _TARGET_INDEX_NUMBER:
        return None
    return _parse_msf(parts[2], lineno)


def parse_cue_sheet(text: str) -> list[float]:
    """Parse CUE-sheet text into cut-point timestamps, in milliseconds.

    Extracts each AUDIO TRACK's INDEX 01 timestamp from a single-FILE CUE
    sheet (the standard shape for a ripped album: one audio file, several
    TRACK/INDEX 01 entries marking where each track starts). The first
    track's INDEX 01 is almost always 00:00:00 and is dropped from the
    output -- it isn't a real cut point, the stream already starts there;
    feeding a leading 0.0 into split_at would otherwise produce a spurious
    empty first segment. Any other collected timestamp, including a
    genuinely nonzero first one, is kept.

    Recognized but ignored: REM comments, TITLE/PERFORMER/SONGWRITER
    (disc- and track-level), CATALOG, CDTEXTFILE, ISRC, FLAGS, PREGAP/
    POSTGAP, INDEX 00 and INDEX 02+, and any TRACK whose type isn't AUDIO
    (its INDEX lines are skipped, not treated as errors).

    Args:
        text: The full contents of a .cue file, already decoded to str.

    Returns:
        Cut-point timestamps in milliseconds, in the order tracks appear,
        ready to pass directly as `split_at`'s `timestamps_ms` argument.
        Empty if the cue sheet describes only a single track.

    Raises:
        CueSheetError: `text` contains no AUDIO TRACK with an INDEX 01
            entry; contains more than one FILE line (multi-FILE cue
            sheets, where each TRACK's audio lives in a different file,
            aren't supported -- their timestamps aren't comparable
            without knowing per-file boundaries); an INDEX 01 timestamp
            isn't valid MM:SS:FF (wrong field count, non-numeric fields,
            seconds outside 0-59, or the CD frame field outside 0-74 at
            75 frames/second); an AUDIO track's block ends without ever
            recording its own INDEX 01; or a later INDEX 01 timestamp is
            strictly less than the one before it (equal, i.e. duplicate,
            timestamps are allowed -- split_at already documents that a
            duplicate timestamp simply yields an empty segment).
    """
    timestamps: list[float] = []
    seen_file = False
    in_audio_track = False
    current_track_number = "?"
    have_index01_for_current_track = False
    lineno = 0

    def _check_track_closed(lineno: int) -> None:
        if in_audio_track and not have_index01_for_current_track:
            raise CueSheetError(f"line {lineno}: TRACK {current_track_number} has no INDEX 01")

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()

        if upper.startswith("FILE "):
            if seen_file:
                raise CueSheetError(f"line {lineno}: multi-FILE cue sheets are not supported")
            seen_file = True
        elif upper.startswith("TRACK "):
            _check_track_closed(lineno)
            current_track_number, in_audio_track = _handle_track_line(line)
            have_index01_for_current_track = False
        elif upper.startswith("INDEX ") and in_audio_track:
            ms = _handle_index_line(line, lineno)
            if ms is not None:
                if timestamps and ms < timestamps[-1]:
                    raise CueSheetError(
                        f"line {lineno}: INDEX 01 timestamps are not increasing "
                        f"({timestamps[-1]} then {ms} ms)"
                    )
                timestamps.append(ms)
                have_index01_for_current_track = True
        # REM/TITLE/PERFORMER/SONGWRITER/CATALOG/CDTEXTFILE/ISRC/FLAGS/
        # PREGAP/POSTGAP, and INDEX lines outside an AUDIO track, or with
        # an index number other than 01 -- all intentionally ignored.

    _check_track_closed(lineno=lineno)

    if not timestamps:
        raise CueSheetError("no audio TRACK/INDEX 01 entries found in cue sheet text")

    if timestamps[0] == 0.0:
        timestamps = timestamps[1:]
    return timestamps
