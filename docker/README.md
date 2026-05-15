# Lea benchmark Docker images

One image per benchmark. Each image bakes:

- Ubuntu 24.04 + elan + the benchmark's pinned Lean toolchain
- Lake project skeleton: `lakefile.*`, `lake-manifest.json`, `lean-toolchain`
- Built `.lake/` (Mathlib + every dep `lake exe cache get` retrieved, ~6–8 GB)
- An empty `testbed/` directory under the project root

The image does NOT bake problem `.lean` files. The runner injects `testbed/Main.lean` at container start, so new problems don't require an image rebuild.

## Capturing agent modifications: federated git diff

Each `.lake/packages/<pkg>/` is its own git repo pinned at the revision recorded in `lake-manifest.json`. Lake relies on those `.git` dirs for manifest verification — do **not** delete them or roll them into an outer git repo. (We tried; it either fights Lake's submodule semantics or makes Lake re-clone every package on next container start, wiping the cache.)

Instead, capture modifications by walking each repo:

```bash
# Outer project (testbed, lakefile, anything not under .lake/)
git -C /work/fqb init -q && git -C /work/fqb add -A . && git -C /work/fqb commit -q -m baseline
git -C /work/fqb diff HEAD

# Each Lake package, using its own pre-existing .git
for pkg in /work/fqb/.lake/packages/*/; do
    git -C "$pkg" diff && true
done
```

Phase 2's capture script combines these into one multi-section patch artifact. Replay applies each section to the appropriate repo.

## Tagging convention

`shaswatpatel123/lea-<bench>:<toolchain>` plus a moving `:latest`.

- `shaswatpatel123/lea-fqb:v4.28.0` + `:latest`
- `shaswatpatel123/lea-putnam:<toolchain>` + `:latest` (toolchain read from PutnamBench's `lean-toolchain`)

A toolchain bump means a new tag, never overwriting an existing one. Old run artifacts must remain replayable against the exact image they were produced with.

## Build

Run from the repo root.

```bash
# FQB
docker build -t shaswatpatel123/lea-fqb:v4.28.0 \
             -t shaswatpatel123/lea-fqb:latest \
             -f docker/fqb.Dockerfile .

# Putnam — first build verifies the PutnamBench subdirectory layout.
# If the `cp` step fails, rebuild with --build-arg PUTNAM_SUBDIR=<actual path>.
docker build -t shaswatpatel123/lea-putnam:latest \
             -f docker/putnam.Dockerfile .
# Re-tag once you've read the toolchain version out of the built image:
TOOLCHAIN=$(docker run --rm shaswatpatel123/lea-putnam:latest cat /work/putnam/lean-toolchain | sed 's|.*:||')
docker tag shaswatpatel123/lea-putnam:latest shaswatpatel123/lea-putnam:$TOOLCHAIN
```

## Sanity tests (run before pushing)

For each image:

```bash
IMG=shaswatpatel123/lea-fqb:v4.28.0  # or :lea-putnam:<toolchain>
PROJ=/work/fqb                    # or /work/putnam

# 1. Toolchain is installed
docker run --rm $IMG bash -c 'lean --version'

# 2. Mathlib is importable from testbed/
docker run --rm $IMG bash -c \
  "cd $PROJ && echo 'import Mathlib' > testbed/Main.lean && lake env lean testbed/Main.lean"

# 3. testbed/ exists and the git baseline is clean
docker run --rm $IMG bash -c \
  "cd $PROJ && [ -d testbed ] && git status --porcelain"

# 4. Mathlib edits are visible via per-package git diff (federated model)
docker run --rm $IMG bash -c "
  cd $PROJ
  F=.lake/packages/mathlib/Mathlib/Init.lean
  echo '-- test edit' >> \$F
  git -C .lake/packages/mathlib diff --quiet -- Mathlib/Init.lean \
    && echo 'FAIL: no diff' \
    || echo 'PASS: real text diff visible'
"
# Expected output: PASS: real text diff visible
```

Test 4 validates the federated-diff capture: each Lake package's own `.git` shows real text hunks for agent edits.

## Push

```bash
docker login                                             # one-time, user's own credentials
docker push shaswatpatel123/lea-fqb:v4.28.0
docker push shaswatpatel123/lea-fqb:latest
docker push shaswatpatel123/lea-putnam:<toolchain>
docker push shaswatpatel123/lea-putnam:latest
```

Each push is ~6–8 GB; on residential upload bandwidth allow 10–30 min per image.

## Rebuilding on toolchain bump

When FormalQualBench or PutnamBench bumps its `lean-toolchain`:

1. Edit the `--default-toolchain` ARG in the relevant Dockerfile (FQB only — Putnam reads it from the upstream repo).
2. Re-build with the new tag, e.g. `shaswatpatel123/lea-fqb:v4.30.0`. Keep the old tag pushed so prior run artifacts stay replayable.
3. Re-point `:latest` only after running the sanity tests against the new image.
