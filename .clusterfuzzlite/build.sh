#!/bin/bash -eu

pip3 install .
compile_python_fuzzer "$SRC/waxcut/.clusterfuzzlite/fuzz_frames.py"
