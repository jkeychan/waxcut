FROM gcr.io/oss-fuzz-base/base-builder-python
RUN pip3 install atheris
COPY . $SRC/waxcut
COPY .clusterfuzzlite/build.sh $SRC/
WORKDIR $SRC/waxcut
