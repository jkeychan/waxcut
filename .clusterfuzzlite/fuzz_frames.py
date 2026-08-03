"""Fuzz harness for waxcut.iter_frames: the only entry point that parses
untrusted raw bytes directly. UnsupportedMp3Error is the only exception
frame parsing should ever raise on malformed input — anything else is a bug.
"""

import sys

import atheris

with atheris.instrument_imports():
    import waxcut


def test_one_input(data: bytes) -> None:
    try:
        frames = waxcut.iter_frames(data)
    except waxcut.UnsupportedMp3Error:
        return

    waxcut.total_duration_ms(frames)
    waxcut.frame_index_at(frames, len(data))
    waxcut.slice_bytes(data, frames, 0, len(frames))


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
