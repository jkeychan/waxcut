# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(pre-1.0: minor bumps may include breaking changes).

## [Unreleased]

### Added

- `WaxcutError`: common base class for waxcut's parse/format errors
  (`UnsupportedMp3Error`, `FileTooLargeError`, `CueSheetError`).
- `Frames`: memory-compact, array-backed sequence type returned by
  `scan_frames` and found on `AudioStream.frames`.
- `split_to_files`: split directly to disk without holding every
  segment in memory at once.

### Changed

- `AudioStream` equality/hashing is now identity-based, not field-wise
  — two `AudioStream`s parsed from the same file are no longer `==`.
- A stepped slice on `Frames` (e.g. `frames[::2]`) now raises
  `TypeError` instead of `ValueError`.
- `write_id3v2_tag` now rejects NUL/CR/LF in `title`/`artist` text with
  `ValueError` instead of passing them through unremarked.
- `scan_frames` now aborts after a bounded number of consecutive failed
  resync attempts, instead of scanning purely-adversarial input all
  the way to `max_size`.

### Fixed

- `scan_frames` no longer discards already-located valid frames when
  the resync-abort bound above trips; it now returns what was found so
  far, matching the natural-exhaustion case. The bound still raises
  `UnsupportedMp3Error` when it trips with zero frames located.

## [0.3.1] - 2026-08-06

### Fixed

- Fixed follow-up issues from the cross-feature review: double-tag
  corruption in `write_id3v2_tag`, broken PyPI README links, and docs
  gaps (#44).

## [0.3.0] - 2026-08-05

### Added

- `parse_cue_sheet`: parse CUE-sheet `TRACK`/`INDEX 01` timestamps for
  use with `split_at` (#37).
- `write_id3v2_tag`: propagate ID3v2 tags to split output (#36).
- `use_mmap` opt-in on `load_audio_stream` for scanning large files
  without fully materializing them in memory (#42).

### Changed

- `scan_frames` now skips non-frame bytes with `bytes.find()` instead
  of a per-byte scan (#34).
- Frame storage is now compact and array-backed: ~0.95x memory
  amplification over input size, down from ~6x (#32).
- CI now enforces `uv.lock` via `uv sync --locked` (#33).

### Fixed

- Fixed the CRC-protected VBR tag offset; added `py.typed` and
  hardened misuse guards (#29).
- Added a 250 MB size limit against resource-amplification attacks
  and deduped header unpacking (#30).

## [0.2.1] - 2026-08-04

### Changed

- Migrated the docs site from Netlify to Cloudflare Pages (#24).
- Pinned the Node version for the docs site build, added baseline
  security headers, and wired up Cloudflare Web Analytics (#25).
- README: made the one-liner example human-editable and added a
  transition to the full usage example (#26); added a `split_at`/
  `join_frames` usage example (#22).

## [0.2.0] - 2026-08-04

### Added

- `split_at`/`join_frames` convenience functions (#21).
- Docusaurus documentation site, initially deployed to GitHub Pages
  and then migrated to Netlify (#15, #16).

### Fixed

- Fixed a negative-index bug in `slice_bytes`, corrected stale docs,
  and dropped a leftover template scaffold (#18).
- Hardened input handling and proved splitting accuracy with
  hash-verified round-trips (#14).

### Changed

- Adopted a maximally-strict ruff rule selection (#11).
- Added an OpenSSF Best Practices badge (100% passing) (#12).

## [0.1.1] - 2026-08-03

### Fixed

- Fixed stale PyPI install instructions in the README and refreshed
  badges (#9).

## [0.1.0] - 2026-08-03

Initial release: frame-accurate, lossless MP3 splitting and duration
parsing in pure Python.

[0.3.1]: https://github.com/jkeychan/waxcut/releases/tag/v0.3.1
[0.3.0]: https://github.com/jkeychan/waxcut/releases/tag/v0.3.0
[0.2.1]: https://github.com/jkeychan/waxcut/releases/tag/v0.2.1
[0.2.0]: https://github.com/jkeychan/waxcut/releases/tag/v0.2.0
[0.1.1]: https://github.com/jkeychan/waxcut/releases/tag/v0.1.1
[0.1.0]: https://github.com/jkeychan/waxcut/releases/tag/v0.1.0
