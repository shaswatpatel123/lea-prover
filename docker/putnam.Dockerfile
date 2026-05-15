FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash lean
USER lean
WORKDIR /home/lean

ARG PUTNAM_REPO=https://github.com/trishullab/PutnamBench.git
# Pinned commit from probe on 2026-05-13. Bump as PutnamBench moves; each
# bump yields a new image tag.
ARG PUTNAM_COMMIT=9bc55b558208db474ce88e1398f9b5a4b09cf003
ARG PUTNAM_SUBDIR=lean4

WORKDIR /work
RUN git clone "$PUTNAM_REPO" putnam-src \
 && cd putnam-src && git checkout "$PUTNAM_COMMIT"

# Stage the Lake metadata from PutnamBench's Lean 4 subproject. Handle either
# `lakefile.lean` (Lake DSL, what PutnamBench currently ships) or `lakefile.toml`.
RUN mkdir -p putnam \
 && cp putnam-src/$PUTNAM_SUBDIR/lake-manifest.json putnam/lake-manifest.json \
 && cp putnam-src/$PUTNAM_SUBDIR/lean-toolchain     putnam/lean-toolchain \
 && if   [ -f putnam-src/$PUTNAM_SUBDIR/lakefile.toml ]; then \
        cp putnam-src/$PUTNAM_SUBDIR/lakefile.toml putnam/lakefile.toml ; \
    elif [ -f putnam-src/$PUTNAM_SUBDIR/lakefile.lean ]; then \
        cp putnam-src/$PUTNAM_SUBDIR/lakefile.lean putnam/lakefile.lean ; \
    else \
        echo "no lakefile in $PUTNAM_SUBDIR" && exit 1 ; \
    fi

# Install elan with whatever toolchain PutnamBench pins (read from the file we just staged).
RUN curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf \
    | sh -s -- -y --default-toolchain "$(cat /work/putnam/lean-toolchain)"
ENV PATH=/home/lean/.elan/bin:$PATH

# Drop the source clone — image only needs the Lake project skeleton + built .lake/.
RUN rm -rf /work/putnam-src

WORKDIR /work/putnam
RUN mkdir -p testbed \
 && lake exe cache get

# No outer-project git baseline; each .lake/packages/<pkg>/ provides its own
# (see notes in docker/fqb.Dockerfile and docker/README.md).

CMD ["sleep", "infinity"]
