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

## Pull request process

- Fork the repo, branch from `main`, open a PR against `main`.
- CI (tests across Python 3.10–3.14, lint, CodeQL, fuzzing) must pass.
- PRs require an approving review before merge — for external contributions
  this means a maintainer review.

## Reporting a vulnerability

See [SECURITY.md](SECURITY.md) — please don't file security issues as public
GitHub issues.
