FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash lean
USER lean
WORKDIR /home/lean

# Toolchain pinned to miniF2F-lean4's lean-toolchain (v4.24.0).
RUN curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf \
    | sh -s -- -y --default-toolchain leanprover/lean4:v4.24.0
ENV PATH=/home/lean/.elan/bin:$PATH

WORKDIR /work
# Only the Lake metadata. No source library, no problem files — those land in
# /work/minif2f/testbed/ at container start, injected by the runner.
COPY --chown=lean:lean miniF2F-lean4/lakefile.lean      minif2f/lakefile.lean
COPY --chown=lean:lean miniF2F-lean4/lake-manifest.json minif2f/lake-manifest.json
COPY --chown=lean:lean miniF2F-lean4/lean-toolchain     minif2f/lean-toolchain

WORKDIR /work/minif2f
RUN mkdir -p testbed \
 && lake exe cache get

# No outer-project git baseline; each .lake/packages/<pkg>/ provides its own
# (see notes in docker/fqb.Dockerfile and docker/README.md).

CMD ["sleep", "infinity"]
