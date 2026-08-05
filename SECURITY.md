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

## Reporting a Vulnerability

Please report security vulnerabilities privately using GitHub's
[private vulnerability reporting](https://github.com/jkeychan/waxcut/security/advisories/new)
rather than filing a public issue.

You should receive an initial response within 14 days. If the report is
confirmed, a fix will be prepared and a security advisory published once a
patched release is available.
