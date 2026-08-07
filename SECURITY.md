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
governed by a separate, larger 2 GB limit instead, which bounds the
*time* cost of a worst-case adversarial scan instead (parsing is O(n) in
file size regardless of what backs the data; measured ~150 MB/s against a
file packed with minimum-size MPEG2.5 frames). Callers using `use_mmap=True`
are responsible for calling `AudioStream.close()` (or using it as a context
manager) when done -- the file handle stays open for the AudioStream's
whole lifetime.

A file packed with minimum-size MPEG2/2.5 Layer III frames (~24 bytes each)
parses without crashing, at a rate proportional to input size — the size
limits above bound that worst case for each mode. Located frames are stored
in compact packed arrays (not individually-allocated objects), measured at
~0.95x memory amplification over input size for an adversarial 10 MB file
built this way — previously ~6x before that storage redesign. See the
[docs site's Security page](https://waxcut.pages.dev/docs/security#resource-limits)
for the full writeup and measurement methodology.

`parse_cue_sheet` has no size limit: it takes an already-materialized
`str`, not a `Path`, so it has no I/O boundary of its own and a cap
wouldn't bound anything the caller doesn't already control. Cue text
amplifies further into parsed timestamps than frame parsing does (~3.9x
measured, vs. ~0.95x above), and 40,000 adversarial/mutated cue inputs run
through it during testing raised only `CueSheetError` -- never an
unhandled exception.

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
