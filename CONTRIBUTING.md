# Contributing

Bug reports and pull requests are welcome via [GitHub Issues](https://github.com/jkeychan/waxcut/issues)
and [Pull Requests](https://github.com/jkeychan/waxcut/pulls).

## Development setup

Requires [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/jkeychan/waxcut
cd waxcut
uv sync
```

## Before opening a PR

```bash
uv run pytest tests/ -v
uv run ruff check .
uv run ruff format --check .
```

All three run in CI and must pass before merge.

## Testing policy

New functionality needs test coverage. For changes to `src/waxcut/frames.py`
specifically, prefer validating against real or synthetic MP3 fixtures and
cross-checking results against [mutagen](https://github.com/quodlibet/mutagen)'s
independent parser where relevant — see `tests/test_frames.py` for the
existing pattern (duration matching, frame contiguity, byte-completeness of
splits, and independent decode validation via `ffmpeg` where available).

## Benchmarking

`bench/benchmark.py` is a standalone throughput diagnostic (not part of
CI/pytest) for changes that touch the parsing hot path:

```bash
uv run python bench/benchmark.py
```

## Documentation

A change to the public API (anything in `waxcut.__all__`) needs a matching
update to [`README.md`](README.md) and
[`site/docs/api-reference.md`](site/docs/api-reference.md) — new
functions/classes, changed signatures, or changed exception behavior should
all be reflected there, not just in code docstrings. The
[`site.yml`](.github/workflows/site.yml) workflow builds the Docusaurus site
on every PR touching `site/**`, and fails the build on a broken internal
link (`onBrokenLinks: 'throw'`), so a docs change that breaks a cross-link
is caught before merge.

## Pull request process

- Fork the repo, branch from `main`, open a PR against `main`.
- CI must pass: tests across Python 3.10–3.14, lint, CodeQL, and the
  `actionlint`/`zizmor`/`ratchet` workflow-security checks run on every PR
  regardless of what changed. ClusterFuzzLite PR fuzzing only runs when a
  PR touches `src/**` or `.clusterfuzzlite/**` — it's not part of every
  PR's CI run.
- PRs require an approving review before merge — for external contributions
  this means a maintainer review.

## Reporting a vulnerability

See [SECURITY.md](SECURITY.md) — please don't file security issues as public
GitHub issues.
