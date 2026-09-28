FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:d74383a516f17ae3300aee7160277adf0702a22ca400d2dc54cd79f4b4c86b70
RUN pip3 install atheris==3.0.0
COPY . $SRC/waxcut
COPY .clusterfuzzlite/build.sh $SRC/
WORKDIR $SRC/waxcut
