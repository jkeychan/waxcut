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
workflow. Every pull request is fuzzed against `scan_frames` — the only
entry point that parses untrusted raw bytes directly — plus the downstream
functions the harness calls on its result (`total_duration_ms`,
`frame_index_at`, `slice_bytes`), with malformed and adversarial byte
sequences: truncated headers, corrupted sync words, bogus bitrate/sample-rate
indices — looking for crashes, hangs, or memory issues rather than
correctness per se. This matters specifically because `scan_frames` reads
raw, untrusted bytes directly (offsets and lengths all come from
attacker-controlled header bits). `load_audio_stream` — and the
Xing/Info/VBRI/LAME-parsing code paths that only run inside it, not in
`scan_frames` alone — is not exercised by this harness, nor are
`write_id3v2_tag` or `parse_cue_sheet`.

## Resource limits

Fuzzing (above) catches crashes and hangs on small mutated inputs within a
CI time budget — it does not exercise deliberately large adversarial input,
which is a different threat: a file packed with minimum-size MPEG2/2.5
Layer III frames (as little as ~24 bytes each) parses in linear time and
never crashes, but produces one located frame per frame found.

This used to cost real memory amplification: `AudioStream.frames` was
originally a `list` of individually-allocated `Frame` objects (~168 bytes
each once you count the object itself plus its boxed int/float fields), so
a 10 MB adversarial file produced ~58 MB of `Frame` objects — ~6x
amplification on top of the input bytes, scaling linearly with input size.
`AudioStream.frames` is now backed by compact packed arrays instead
(`Frames`, ~24 bytes/frame, unboxed), with individual `Frame` objects
constructed lazily only when you actually index into or iterate the
sequence. Re-measured on the same adversarial construction: the same 10 MB
file now produces **~9.4 MB** of `Frames` storage — amplification of
**~0.95x**, i.e. the parsed structure uses *less* memory than the input
itself.

That removes amplification as a concern, but a single call still costs
real, bounded time and memory proportional to input size — `scan_frames`
and `load_audio_stream` both reject input over **250 MB** by default,
raising [`FileTooLargeError`](./api-reference.md#filetoolargeerror), so a
single call's worst-case cost stays bounded regardless. A `load_audio_stream`
call checks the file's size on disk *before* reading it, so an oversized
file is never fully loaded into memory in the first place.

`load_audio_stream(path, use_mmap=True)` changes that calculus. Instead of
reading the file into a Python `bytes` object, it memory-maps it, so the
250 MB default's memory-cost rationale doesn't apply — the file's bytes
are never materialized in Python's heap in the first place, the OS pages
them in on demand. What still applies is *time*: parsing is O(n) in file
size no matter what backs the bytes, so a large enough mmap'd file still
costs real wall-clock time to scan. `use_mmap=True` is therefore governed
by its own, larger **2 GB** limit, sized to bound that worst-case scan time
rather than memory — measured at roughly **150 MB/s** against the same
adversarial construction described above (a file packed edge-to-edge with
minimum-size MPEG2.5 frames), so a 2 GB adversarial input costs on the
order of ten seconds to scan rather than being unbounded.

Because the file stays memory-mapped for as long as the `AudioStream` is
alive, callers using `use_mmap=True` are responsible for calling
`AudioStream.close()` (or using it as a context manager) when they're done
with it — unlike the non-mmap path, where the file handle is closed once
the bytes are read, the mmap'd file's handle stays open for the
`AudioStream`'s whole lifetime.

`use_mmap=True` is exercised in CI on Linux only — the [`ci.yml`](https://github.com/jkeychan/waxcut/actions/workflows/ci.yml)
workflow runs exclusively on `ubuntu-latest`, so the mmap code path isn't
independently verified on Windows or macOS. `mmap`'s underlying semantics
differ enough across platforms (page-alignment behavior, file-locking
interaction, close-on-exec) that this is worth calling out explicitly
rather than assuming portability.

Neither limit is currently a configurable parameter — if your use case
legitimately needs to process larger files, please
[open an issue](https://github.com/jkeychan/waxcut/issues/new) rather than
relying on undocumented internals to work around it.

`parse_cue_sheet` (untrusted `.cue` file text) is a different case, and
deliberately has no size cap. `scan_frames`/`load_audio_stream` read from a
`Path`, so they need to reject an oversized file before ever reading it off
disk into memory. `parse_cue_sheet` takes an already-materialized `str` --
by the time it's called, the caller has already paid the cost of holding
that text in memory, so a cap inside `parse_cue_sheet` wouldn't bound
anything the caller doesn't already control. This is a deliberate
consequence of the two functions sitting at different I/O boundaries, not
an oversight.

For completeness, the amplification from cue text to parsed timestamps is
higher than the ~0.95x figure above for frame parsing — measured at
roughly **3.9x**: a 5.93 MB cue sheet producing 99,999 timestamps peaks
around 22.9 MB. Robustness has been checked the same way as the frame
parser, if not yet via the same continuous ClusterFuzzLite harness: 40,000
adversarial and mutated cue inputs run through `parse_cue_sheet` produced
zero exceptions other than the documented `CueSheetError`.

## `write_id3v2_tag`'s input guards

`write_id3v2_tag` is the one function in the public surface that writes
text supplied by the caller — a title, artist, or track number — into
binary tag frames, rather than only reading and validating bytes handed to
it. That text isn't necessarily hand-typed: a realistic pipeline pulls it
straight from `parse_cue_sheet`'s `TITLE`/`PERFORMER` fields, which are
themselves attacker-influenceable if the `.cue` file came from an untrusted
source. Two guards keep that path bounded and unambiguous:

- **Size**: the combined `TIT2`/`TPE1`/`TRCK` frame payload is capped by
  the ID3v2 tag format's own size field — a 4-byte syncsafe integer, whose
  maximum representable value is `2**28 - 1` (~256 MB). A pathologically
  large title (e.g. a caller accidentally passing an entire file's
  contents as `title`) raises `ValueError` rather than silently truncating
  or overflowing.
- **No stacked tags**: if `data` already starts with an ID3v2 tag,
  `write_id3v2_tag` raises `ValueError` instead of prepending a second one.
  This isn't just a correctness fix — a stacked tag shifts where frame
  scanning actually starts, so `scan_frames` (which only skips *one*
  leading tag) would misinterpret real audio bytes as tag-adjacent data.
  That's a parser-confusion bug class, the same family fuzzing (above)
  exists to catch, even though this particular case is a deterministic
  input-validation guard rather than something fuzzing found.

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
