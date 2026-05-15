FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash lean
USER lean
WORKDIR /home/lean

# Toolchain pinned to FormalQualBench's lean-toolchain (v4.28.0).
RUN curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf \
    | sh -s -- -y --default-toolchain leanprover/lean4:v4.28.0
ENV PATH=/home/lean/.elan/bin:$PATH

WORKDIR /work
# Only the Lake metadata. No source library, no problem files — those land in
# /work/fqb/testbed/ at container start, injected by the runner.
COPY --chown=lean:lean FormalQualBench/lakefile.toml      fqb/lakefile.toml
COPY --chown=lean:lean FormalQualBench/lake-manifest.json fqb/lake-manifest.json
COPY --chown=lean:lean FormalQualBench/lean-toolchain     fqb/lean-toolchain

WORKDIR /work/fqb
RUN mkdir -p testbed \
 && lake exe cache get

# No outer-project git baseline. Each .lake/packages/<pkg>/ is already its own
# git repo pinned at the manifest's revision — Phase 2's capture script walks
# them with `git -C <pkg> diff` to produce real text diffs of any agent edits.
# (We tried initializing an outer baseline that absorbed package contents; it
# either fights Lake's submodule semantics or breaks Lake's manifest verification
# on next container start. Federated per-package diffs are the clean answer.)

CMD ["sleep", "infinity"]
