# Regression fixtures

Binary inputs that previously crashed or hung the parser (found by
ClusterFuzzLite or manual testing), saved here so they never regress. Each
file is referenced by test_regression_corpus_does_not_crash in
tests/test_frames.py, which runs every `*.bin` file here through both
`scan_frames` and `load_audio_stream`, asserting a specific outcome per
file where one is known (see the table below), and otherwise the smoke-test
minimum: parsing must raise nothing but `UnsupportedMp3Error` (or its
`FileTooLargeError` subclass), never an unhandled exception. That test's
parametrize list is asserted non-empty at collection time -- an empty
corpus fails the suite loudly instead of silently showing up as a skip.

This corpus does not include the ClusterFuzzLite artifacts referenced by
earlier commits (those live in GitHub Actions run artifacts); the files
below were constructed by hand, in the same spirit as the synthetic-frame
edge-case tests in `test_frames.py`, to exercise known-tricky code paths in
this codebase's history.

| File | Targets |
| --- | --- |
| `truncated_id3v2_tag.bin` | An ID3v2 header whose syncsafe size field claims a ~1000-byte tag body, but the file is only 30 bytes total. `id3v2_size()` trusts the claimed size and returns an offset far past EOF; `scan_frames` must not choke on that (relies on `bytes.find` returning `-1`, not raising, when `start` exceeds the buffer length -- pinned down here as a regression rather than left as incidental stdlib behavior). |
| `truncated_vbr_flags_word.bin` | The smallest possible Layer III frame (MPEG2, 8kbps, 22050Hz, stereo, no CRC -- 26 bytes) with a `Xing` tag whose 4-byte flags word runs past EOF. `scan_frames` doesn't touch Xing parsing at all (that's `load_audio_stream`-only), so this fixture succeeds at that layer; the guarded read in `_parse_lame_gapless` is what's under test via `load_audio_stream` -- an unguarded read here used to leak a raw `struct.error` out of the public API instead of `UnsupportedMp3Error`. |
| `vbr_header_only_no_audio.bin` | One single, fully valid Xing header frame (MPEG1, 128kbps, 44100Hz, stereo, no CRC -- 417 bytes, flags=0, no LAME extension) with nothing after it. `load_audio_stream` excludes the VBR header frame from `AudioStream.frames`, so a file that's *only* that frame must raise `UnsupportedMp3Error` ("File contains only a VBR header frame, no audio.") rather than return a stream with zero frames. |
| `frame_length_past_eof.bin` | A single valid-looking MPEG1/128kbps/44100Hz/stereo frame header (computed length 417 bytes) followed by only 6 more bytes. `scan_frames` must reject `offset + length > len(data)` and resync past it instead of reading off the end of the buffer. |
| `adversarial_0xff_no_sync.bin` | 4KB of pure `0xFF` bytes. Every 4-byte window passes the sync-mask check trivially but fails layer validation, forcing a full failed `_parse_header` call per byte -- the "carpet of 0xFF" adversarial case `scan_frames`' `_MAX_CONSECUTIVE_RESYNC_FAILURES` guards against. Deliberately small (4KB, not the multi-MB size used for the perf benchmark elsewhere): at this size the scan exhausts the whole buffer and raises via the ordinary "no frames found" path rather than tripping the 2,000,000-consecutive-failure bound, so this fixture targets "never forms a valid sync" specifically, not the resync-count cap (which has its own coverage from the fix that introduced it). |

When adding a new fixture: name it for what it exercises, add a row to the
table above explaining which past bug or code path it targets, and (if you
know the specific expected outcome, not just "doesn't crash") add an entry
to `_SCAN_FRAMES_EXPECTATIONS`/`_LOAD_AUDIO_STREAM_EXPECTATIONS` in
`tests/test_frames.py`.
