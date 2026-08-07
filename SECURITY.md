# Security Policy

## Supported Versions

waxcut is pre-1.0 and only the latest released version is supported with
security fixes.

## Resource limits

`scan_frames`/`load_audio_stream` reject input over 250 MB by default,
raising `FileTooLargeError`. `load_audio_stream` checks the file's size on
disk before reading it, so an oversized file is never fully loaded into
memory in the first place. This bounds the memory cost of holding the
entire file as a Python `bytes` object.

`load_audio_stream(path, use_mmap=True)` memory-maps the file instead of
reading it into memory, so that memory-cost rationale doesn't apply -- it's
governed by a separate, larger 2 GB limit instead, sized around *time* cost
rather than memory. Parsing is O(n) in file size regardless of what backs
the data, but the actual worst case depends heavily on the input's shape: a
file packed with valid minimum-size frames scans at roughly 90 MB/s on this
author's development machine (`bench/security_claims.py`, item 2 -- timings
are hardware-dependent; re-run locally rather than treating this as a
portable number), while a buffer that never forms a valid sync at all (e.g.
a carpet of `0xFF` bytes) is far slower per byte -- around 5.5 MB/s in the
same benchmark (item 3), since every byte forces a failed header-parse
attempt. At that rate, scanning the full 2 GB cap byte-by-byte would take
several minutes. `scan_frames` never actually gets there, though:
`_MAX_CONSECUTIVE_RESYNC_FAILURES` aborts the scan after 2,000,000
consecutive failed resync attempts -- about 360 ms in the same benchmark --
long before byte count alone would force the issue. The 2 GB cap is safe
against the adversarial case because of that bound, not because the raw
scan rate is fast enough to finish in bounded time on its own. Callers
using `use_mmap=True` are responsible for calling `AudioStream.close()` (or
using it as a context manager) when done -- the file handle stays open for
the AudioStream's whole lifetime.

`use_mmap=True` is exercised in CI on Linux only -- the
[`ci.yml`](https://github.com/jkeychan/waxcut/actions/workflows/ci.yml)
workflow runs exclusively on `ubuntu-latest`, so the mmap code path isn't
independently verified on Windows or macOS. `mmap`'s underlying semantics
differ enough across platforms (page-alignment behavior, file-locking
interaction, close-on-exec) that this is worth calling out explicitly
rather than assuming portability.

A file packed with minimum-size MPEG2/2.5 Layer III frames (~24 bytes each)
parses without crashing, at a rate proportional to input size — the size
limits above bound that worst case for each mode. Located frames are stored
in compact packed arrays (not individually-allocated objects): for a 10 MB
adversarial file built from the smallest legal frame this parser accepts
(24 bytes), that storage measures roughly ~1.0x the input size (item 1 in
`bench/security_claims.py`) — an equivalent `list[Frame]` of individually
allocated objects measures roughly ~5.3x for the same input, which is what
this design replaced. See the
[docs site's Security page](https://waxcut.pages.dev/docs/security#resource-limits)
for the full writeup, and `bench/security_claims.py` for the script these
numbers came from.

`parse_cue_sheet` has no size limit: it takes an already-materialized
`str`, not a `Path`, so it has no I/O boundary of its own and a cap
wouldn't bound anything the caller doesn't already control. Cue text
amplifies further into parsed timestamps than frame parsing does — a
~6.5 MB cue sheet producing ~100,000 timestamps peaks around ~3.8x the
input size (item 4 in `bench/security_claims.py`). Robustness against
malformed cue text has been checked by hand-constructed edge cases (see
`tests/test_cue_sheet.py`) rather than a continuous fuzzing harness — unlike
`scan_frames`, `parse_cue_sheet` isn't yet wired into ClusterFuzzLite (see
[Security](https://waxcut.pages.dev/docs/security#continuous-fuzzing) for
exactly what the fuzzing harness does and doesn't cover).

`write_id3v2_tag` writes attacker-influenceable text (a caller-supplied
title/artist, which may itself originate from untrusted `.cue` metadata)
into binary tag frames. Several guards keep that bounded and unambiguous:
the combined frame payload is capped at the ID3v2 tag-size field's own
limit (a 4-byte syncsafe integer, ~256 MB), raising `ValueError` if
exceeded; `write_id3v2_tag` raises `ValueError` rather than writing a
second tag if `data` already has a leading ID3v2 tag, since a stacked tag
would shift where frame scanning starts and corrupt output — a
parser-confusion bug class, not just a correctness one; and NUL/CR/LF are
rejected outright in title/artist text, since they'd otherwise pass
through Latin-1/UTF-16 encoding unremarked and make stored content
silently differ from displayed content.

Known limitation: `write_id3v2_tag` does not implement ID3v2
unsynchronisation (the spec-defined scheme that guarantees a false MPEG
sync pattern can never occur inside a tag body, by inserting a 0x00 byte
after every 0xFF byte and setting a flag telling compliant readers to
undo that). A crafted title/artist could in principle produce a false
sync word (e.g. `0xFF 0xFB`) inside the written tag. waxcut itself is
unaffected — it always skips the tag via `id3v2_size` before scanning for
frames — but a non-compliant player that scans for sync words without
first parsing the ID3v2 header could misdecode tag bytes as audio ahead
of the real content. Tracked as a follow-up, not implemented yet.

## Reporting a Vulnerability

Please report security vulnerabilities privately using GitHub's
[private vulnerability reporting](https://github.com/jkeychan/waxcut/security/advisories/new)
rather than filing a public issue.

You should receive an initial response within 14 days. If the report is
confirmed, a fix will be prepared and a security advisory published once a
patched release is available.
