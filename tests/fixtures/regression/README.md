# Regression fixtures

Binary inputs that previously crashed or hung the parser (found by
ClusterFuzzLite or manual testing), saved here so they never regress.
Each file should be referenced by test_regression_corpus_does_not_crash
in tests/test_frames.py — that test doesn't assert anything about the
*output* (these aren't necessarily valid MP3s), only that parsing them
raises UnsupportedMp3Error or succeeds, never an unhandled exception.
