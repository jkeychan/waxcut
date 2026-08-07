#!/bin/bash -eu

pip3 install .
compile_python_fuzzer "$SRC/waxcut/.clusterfuzzlite/fuzz_frames.py"
cp "$SRC/waxcut/.clusterfuzzlite/fuzz_frames.dict" "$OUT/fuzz_frames.dict"
cp "$SRC/waxcut/.clusterfuzzlite/fuzz_frames.options" "$OUT/fuzz_frames.options"
