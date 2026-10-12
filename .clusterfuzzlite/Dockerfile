FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:756c7196c97d62a66c448e70bdeecce400e68ae2ee96d4393cb38d58ffca18d2
RUN pip3 install atheris==3.0.0
COPY . $SRC/waxcut
COPY .clusterfuzzlite/build.sh $SRC/
WORKDIR $SRC/waxcut
