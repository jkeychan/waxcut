---
sidebar_position: 4
---

# Security

waxcut parses untrusted, attacker-controllable binary input (arbitrary MP3
files) with no external decoder in the loop, so the parser itself is the
attack surface. Here's how that risk is managed, and how to report a
problem.

## Reporting a vulnerability

Please report security vulnerabilities privately using GitHub's
[private vulnerability reporting](https://github.com/jkeychan/waxcut/security/advisories/new)
rather than filing a public issue.

You should receive an initial response within 14 days. If the report is
confirmed, a fix will be prepared and a security advisory published once a
patched release is available.

waxcut is pre-1.0, and only the latest released version is supported with
security fixes — see [SECURITY.md](https://github.com/jkeychan/waxcut/blob/main/SECURITY.md)
in the repository for the current policy.

## Continuous fuzzing

The frame parser is fuzzed continuously with
[ClusterFuzzLite](https://github.com/jkeychan/waxcut/blob/main/.clusterfuzzlite/),
via the
[`cflite_pr.yml`](https://github.com/jkeychan/waxcut/actions/workflows/cflite_pr.yml)
workflow. Every pull request is fuzzed against the parsing entry points with
malformed and adversarial byte sequences — truncated headers, corrupted
sync words, bogus bitrate/sample-rate indices, malformed ID3v2/Xing/Info/VBRI
tags — looking for crashes, hangs, or memory issues rather than correctness
per se. This matters specifically because `load_audio_stream` and
`iter_frames` read raw, untrusted bytes directly (offsets, lengths, and tag
fields all come from attacker-controlled header bits).

## Supply-chain and process posture

waxcut's security posture is checked and scored by two independent,
automated programs:

- **[OpenSSF Scorecard](https://scorecard.dev/viewer/?uri=github.com/jkeychan/waxcut)**
  — evaluates the repository against a set of automated security health
  checks (branch protection, dependency pinning, CI configuration, and
  more) and publishes a score.
- **[OpenSSF Best Practices](https://www.bestpractices.dev/projects/13947)**
  — a self-assessed but publicly verifiable checklist covering the OpenSSF
  Best Practices Badge criteria (change control, quality, security). waxcut
  is currently at 100% passing.

Ordinary CI — build, lint, and the full test suite, including the
mutagen/ffmpeg cross-validation described in
[How It Works](./how-it-works.md#validation) — runs on every pull request
via the [`ci.yml`](https://github.com/jkeychan/waxcut/actions/workflows/ci.yml)
workflow.
