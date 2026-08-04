# Security Policy

## Supported Versions

waxcut is pre-1.0 and only the latest released version is supported with
security fixes.

## Resource limits

`scan_frames`/`load_audio_stream` reject input over 250 MB, raising
`FileTooLargeError`. A file packed with minimum-size MPEG2/2.5 Layer III
frames (~24 bytes each) parses without crashing but produces one `Frame`
object per frame — measured directly, a 10 MB adversarial file produces
~58 MB of `Frame` objects, and that scales linearly. Unbounded, that's a
cheap CPU/memory amplification lever for any service that accepts
user-uploaded "MP3" files and parses them without its own size limit.
`load_audio_stream` checks the file's size on disk before reading it, so
an oversized file is never fully loaded into memory. See the
[docs site's Security page](https://waxcut.pages.dev/docs/security#resource-limits)
for the full writeup.

## Reporting a Vulnerability

Please report security vulnerabilities privately using GitHub's
[private vulnerability reporting](https://github.com/jkeychan/waxcut/security/advisories/new)
rather than filing a public issue.

You should receive an initial response within 14 days. If the report is
confirmed, a fix will be prepared and a security advisory published once a
patched release is available.
