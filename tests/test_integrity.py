"""Hash-verified proof that N-way splitting is lossless: reassembling
every split part reproduces the original audio bytes exactly."""

import hashlib
from itertools import pairwise
from pathlib import Path

import pytest

import waxcut

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_NAMES = [
    "cbr_stereo.mp3",
    "cbr_mono.mp3",
    "vbr_stereo.mp3",
    "lame_vbr_stereo.mp3",
]


@pytest.fixture(params=FIXTURE_NAMES)
def fixture_path(request) -> Path:
    return FIXTURES / request.param


@pytest.mark.parametrize("num_splits", [1, 2, 3, 7])
def test_n_way_split_reassembles_to_identical_hash(fixture_path, num_splits):
    stream = waxcut.load_audio_stream(fixture_path)
    duration = stream.playable_duration_ms

    whole = waxcut.slice_bytes(stream.data, stream.frames, 0, len(stream.frames))
    expected_hash = hashlib.sha256(whole).hexdigest()

    # num_splits evenly spaced cut points across the track
    points = [duration * i / (num_splits + 1) for i in range(1, num_splits + 1)]
    idxs = [0, *(waxcut.frame_index_at(stream.frames, p) for p in points), len(stream.frames)]
    idxs = sorted(set(idxs))  # dedupe cut points that landed on the same frame

    reassembled = b"".join(
        waxcut.slice_bytes(stream.data, stream.frames, start, end)
        for start, end in pairwise(idxs)
        if start < end
    )

    assert hashlib.sha256(reassembled).hexdigest() == expected_hash
    assert reassembled == whole
