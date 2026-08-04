# Security Policy

## Supported Versions

waxcut is pre-1.0 and only the latest released version is supported with
security fixes.

## Resource limits

`scan_frames`/`load_audio_stream` reject input over 250 MB, raising
`FileTooLargeError`. `load_audio_stream` checks the file's size on disk
before reading it, so an oversized file is never fully loaded into memory.

A file packed with minimum-size MPEG2/2.5 Layer III frames (~24 bytes each)
parses without crashing, at a rate proportional to input size — the 250 MB
limit bounds that worst case. Located frames are stored in compact packed
arrays (not individually-allocated objects), measured at ~0.95x memory
amplification over input size for an adversarial 10 MB file built this way
— previously ~6x before that storage redesign. See the
[docs site's Security page](https://waxcut.pages.dev/docs/security#resource-limits)
for the full writeup and measurement methodology.

## Reporting a Vulnerability

Please report security vulnerabilities privately using GitHub's
[private vulnerability reporting](https://github.com/jkeychan/waxcut/security/advisories/new)
rather than filing a public issue.

You should receive an initial response within 14 days. If the report is
confirmed, a fix will be prepared and a security advisory published once a
patched release is available.
