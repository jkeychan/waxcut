FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:855167924712ac98dedabf0ae44071caccbb0130bb0544ea06522b1f82288371
RUN pip3 install atheris==3.0.0
COPY . $SRC/waxcut
COPY .clusterfuzzlite/build.sh $SRC/
WORKDIR $SRC/waxcut
