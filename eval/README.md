# Lea evaluation

How Lea is evaluated on Lean 4 benchmarks (FormalQualBench, miniF2F, Putnam), and how to reproduce results from captured artifacts without rerunning the agent.

## TL;DR

```bash
# Run the parallel evaluator (Docker required, one container per attempt)
GOOGLE_API_KEY=...  uv run python -m eval.run_minif2f_parallel \
    --limit 3 --workers 2 --max-turns 20 --model gemini-3.1-pro-preview

# Verify a captured result in a fresh container — no agent, no API key needed
uv run python -m eval.replay eval/sample_artifact \
    --image shaswatpatel123/lea-minif2f:v4.24.0 --project-root /work/minif2f
# → RESULT: PASS
```

The bundled sample artifact in `eval/sample_artifact/` is ~1 KB and works end-to-end on any machine with Docker + a pull of `shaswatpatel123/lea-minif2f:v4.24.0`.

## What's here

Two runner styles for the same benchmarks:

| Mode | Scripts | Isolation | Parallelism | Replayable |
|---|---|---|---|---|
| **Parallel (Docker)** | `run_fqb_parallel.py`, `run_minif2f_parallel.py` | Each attempt = throwaway container | `ThreadPoolExecutor(max_workers=N)` | Yes — artifact tarball |
| **Serial (host)** | `run_fqb.py`, `run_minif2f.py`, `run_putnam.py`, `run_fqb_best_of_n.py`, `run_baseline.py` | Shared host project | None | No |

Use the parallel runners for actual evaluation runs. The serial runners still work and are kept for backward compat and for quick iteration when you don't want Docker overhead.

### What the parallel mode buys you

1. **No cross-attempt contamination.** Every attempt boots from the same baseline image and is thrown away. There's no way for problem A's agent to leak state into problem B, even if A modifies Mathlib.
2. **Parallelism.** `--workers N` runs N agents concurrently, each in its own container. Wall time scales close to `1/N` modulo the slowest single attempt.
3. **Replayable artifacts.** Every attempt produces a `modifications.tar.gz` that contains the agent's edits. Anyone with Docker + the right image can re-verify the result without rerunning the agent (no model API call needed).

The trade-off: each parallel run needs ~10–14 GB of disk per active container, ~3–4 GB of RAM during compile, and Docker installed.

## Quick start (parallel mode)

### Prerequisites

- Docker daemon running
- A Lea-flavored image for the benchmark you're running (pull or build — see "Docker images" below)
- An API key for the provider you're using (`GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY`)

### Running

```bash
# FormalQualBench (Lean 4.28.0)
docker pull shaswatpatel123/lea-fqb:v4.28.0
GOOGLE_API_KEY=...  uv run python -m eval.run_fqb_parallel \
    --workers 2 --attempts 5 --max-turns 40 --model gemini-3.1-pro-preview

# miniF2F (Lean 4.24.0)
docker pull shaswatpatel123/lea-minif2f:v4.24.0
GOOGLE_API_KEY=...  uv run python -m eval.run_minif2f_parallel \
    --workers 2 --attempts 1 --max-turns 20 --model gemini-3.1-pro-preview
```

Output lands under `eval/results/<benchmark>_parallel_<timestamp>/`:

```
results/minif2f_parallel_20260515-221700/
├── preds.json                                  ← outcomes index — NOT edits (see below)
└── <problem>/attempt_<i>/
    ├── start.lean                              ← the .lean injected into testbed/Main.lean
    ├── modifications.tar.gz                    ← all agent edits, federated diff (see below)
    ├── transcript.json                         ← turn-by-turn agent trace + token usage
    ├── verify.txt                              ← full `lake env lean testbed/Main.lean` output
    └── verify.json                             ← {success, detail, time_s, turns}
```

`preds.json` writes are guarded by `threading.Lock` so concurrent workers can't race on it.

### What `preds.json` tracks (and doesn't)

`preds.json` is an **outcomes index**, not an edits manifest. It records, per `(problem, attempt)`, only the verification metadata:

```json
{
  "lea_smoke_trivial": {
    "attempt_0": {
      "success": true,
      "detail": "OK",
      "time_s": 26.0,
      "turns": 3,
      "model": "gemini-3.1-pro-preview"
    }
  },
  ...
}
```

Five fields per attempt: `success`, `detail` (short reason, first ~200 chars of compile output or rejection reason), `time_s`, `turns`, `model`. **No file content, no list of edited files.**

The actual list of files the agent touched (anywhere — including inside `.lake/packages/mathlib/`) lives in **`<attempt_i>/modifications.tar.gz/manifest.json`**:

```json
{
  "project_root": "/work/minif2f",
  "repos": [
    { "repo": ".", "safe": "outer",
      "modified": ["testbed/Main.lean"],
      "new": ["testbed/Helper.lean"] },
    { "repo": ".lake/packages/mathlib", "safe": "_lake__packages__mathlib",
      "modified": ["Mathlib/Analysis/Calculus/MyLemma.lean"],
      "new": [] }
  ]
}
```

The `repos` list has one entry per git repo that has changes — the outer project plus any `.lake/packages/<pkg>/` the agent touched. If you want to mine "which attempts touched Mathlib?", iterate over `modifications.tar.gz` manifests and check `repo` for a `.lake/packages/` prefix.

## Replay: verify any captured result without rerunning the agent

This is the key property of the parallel runner. The artifact tarball (~1 KB for a one-line proof, ~few KB up to a few MB if the agent edits Mathlib) is enough to reproduce the original compile result on any machine.

### Try it on the bundled sample artifact

```bash
docker pull shaswatpatel123/lea-minif2f:v4.24.0
uv run python -m eval.replay eval/sample_artifact \
    --image shaswatpatel123/lea-minif2f:v4.24.0 --project-root /work/minif2f
```

Expected output:

```
Restored 1 file(s) from modifications.tar.gz.
=== compile log ===
OK

=== final proof (head) ===
-- Synthetic problem added by lea/eval/run_minif2f_parallel smoke test.
...
theorem lea_smoke_trivial : 1 + 1 = 2 := by
  rfl

RESULT: PASS
```

That's an actual captured agent run: Gemini 3.1 Pro Preview ran in a `lea-minif2f` container, replaced `sorry` with `rfl`, and the parallel runner wrote the artifact to disk. The replay tool reproduces the verdict in a fresh container with zero agent involvement and zero model API calls.

### What's inside `eval/sample_artifact/`

```
start.lean                — 193 B  : the original problem statement (sorry-ed)
modifications.tar.gz      — 809 B  : the agent's edits
transcript.json           — 2.5 KB : agent's turn-by-turn record + token usage
verify.json               —  71 B  : {success: true, detail: "OK", time_s: 26.0, turns: 3}
```

Pop `modifications.tar.gz` open if you want to see the federated capture format yourself:

```
modifications/
├── manifest.json             — {project_root, repos: [{repo, head_at_snap, modified, new}]}
├── patches/
│   └── outer.diff            — git unified diff (human review)
└── files/
    └── outer/
        └── testbed/
            └── Main.lean     — full content (replay fidelity)
```

For every git repo affected (the outer project plus each `.lake/packages/<pkg>/`), the tar carries both a unified diff (for review) and a full copy of every changed/new file (for replay). Replay uses `files/`; the diffs are review-only.

## Design choices and why

### 1. Docker container per attempt (not per problem, not per run)

Each individual attempt boots from the same baseline image and is torn down after. No shared mutable state can leak between attempts, even within the same problem. We follow [mini-swe-agent's `swebench.py`](../mini-swe-agent/src/minisweagent/run/benchmarks/swebench.py) lifecycle: `docker run -d --rm --name lea-<uuid> sleep infinity` on start, async `docker stop && docker rm` on cleanup.

Why not container-per-problem (and re-use across attempts)? Because best-of-N means re-running the same problem; sharing state across attempts could let attempt N+1 inherit attempt N's Mathlib edits. Independence is more important than the few seconds of container-start overhead.

### 2. Image = Lake project skeleton + built `.lake/` + empty `testbed/`; problem files injected at run time

The image bakes the heavyweight stuff (toolchain + Mathlib oleans, fetched via `lake exe cache get`) and an empty `testbed/` directory under the project root. Per attempt, the runner:

1. `docker cp`s the problem's starting `.lean` into `testbed/Main.lean`.
2. Initializes a top-level git repo with `.gitignore` excluding `.lake/`, then commits the baseline.
3. Snapshots the HEAD sha of the outer repo AND of each `.lake/packages/<pkg>/`.
4. Runs the agent. Captures via federated diff (see #3).
5. Verifies inside the container, writes artifacts to the host, cleans up.

Adding a new problem requires no image rebuild — drop a `.lean` file in `MiniF2F/Valid/` (or whichever split) and the runner picks it up.

### 3. Federated git diff for modification capture

The first design attempt was to roll everything into one outer git baseline (delete the `.git` directories inside each `.lake/packages/<pkg>/` so the outer git tracks all package source as plain files). This **breaks Lake**: on the next container start, Lake notices its package metadata is gone, decides the deps have "moved", and re-clones every package from scratch — wiping the prebuilt olean cache and leaving the container unable to compile.

The working answer: leave Lake's embedded package gits intact and diff per-repo at capture time.

```python
# At capture time, for every repo in the snapshot:
for repo, head_at_snap in snap["repos"].items():
    modified = `git -C <repo> diff --name-only HEAD`
    new      = `git -C <repo> ls-files --others --exclude-standard`
    patch    = `git -C <repo> diff HEAD`
    # Pack into tarball: patches/<safe>.diff + files/<safe>/<rel> for every changed file
```

This catches edits inside `.lake/packages/mathlib/Mathlib/...` (Mathlib lemma additions) just as cleanly as edits under `testbed/`. Validated end-to-end in `tests/runner/test_harness.py`.

### 4. Tarball stores both unified diffs AND full file copies

Patches can fail to apply if the baseline ever drifts (different Mathlib pin, line-number sensitivity, etc.). Full file copies are byte-authoritative. We keep both:

- `patches/<safe>.diff` for human review (small, readable)
- `files/<safe>/<rel>` for replay (authoritative, restores file content directly)

Replay uses `files/`; nothing in the replay path depends on `git apply` succeeding.

### 5. Threading, not multiprocessing

Each parallel worker spends ~all its time in `docker exec` (subprocess wait) and LLM API (network wait). Threads work fine, the GIL isn't a bottleneck, and shared in-process state (`preds.json` writes via `threading.Lock`) is simpler than coordinating across processes. Same pattern as mini-swe-agent's `swebench.py:271`.

### 6. The agent edits `testbed/Main.lean` in place

The agent receives the problem statement (already containing `:= by sorry`), and edits that same file to fill in the proof. The verify step runs `lake env lean testbed/Main.lean`. Cleaner than the old serial pattern (write to a separate `eval_proofs/<name>.lean`) because there's exactly one target file and the path is uniform across all benchmarks and problems.

### 7. Phase 1 baked the images first; Phase 2 added the env layer + parallel runner

The Docker images were Phase 1 because they're the slowest, network-bound, hard-to-iterate piece. They're now public on DockerHub:

- [`shaswatpatel123/lea-fqb:v4.28.0`](https://hub.docker.com/r/shaswatpatel123/lea-fqb) (FormalQualBench, ~15 GB)
- [`shaswatpatel123/lea-putnam:v4.27.0`](https://hub.docker.com/r/shaswatpatel123/lea-putnam) (PutnamBench, ~15 GB)
- [`shaswatpatel123/lea-minif2f:v4.24.0`](https://hub.docker.com/r/shaswatpatel123/lea-minif2f) (miniF2F, ~13 GB)

Phase 2 was the in-process refactor: a thin `Environment` protocol (`lea/env/`), tool routing through that env (`lea/tools.py`'s `build_handlers(env)` factory), a new optional `env` parameter on `lea.agent.run()` defaulting to `LocalEnvironment(cwd)` for backward compat, and the parallel runners + replay tool.

## Docker images

### How to pull

```bash
# FormalQualBench (Lean 4.28.0, ~15 GB)
docker pull shaswatpatel123/lea-fqb:v4.28.0

# PutnamBench (Lean 4.27.0, ~15 GB)
docker pull shaswatpatel123/lea-putnam:v4.27.0

# miniF2F (Lean 4.24.0, ~13 GB)
docker pull shaswatpatel123/lea-minif2f:v4.24.0
```

Each pull is a one-time cost per machine (Docker caches layers locally). First pull on residential bandwidth is 10–30 min depending on the image; subsequent runs reuse the cache and start a container in seconds. Total disk for all three: ~43 GB.

Verify the pull landed:

```bash
docker images 'shaswatpatel123/lea-*'
# REPOSITORY                       TAG       IMAGE ID       SIZE
# shaswatpatel123/lea-fqb          v4.28.0   ...            14.6GB
# shaswatpatel123/lea-putnam       v4.27.0   ...            14.5GB
# shaswatpatel123/lea-minif2f      v4.24.0   ...            13.1GB
```

Smoke-test with the bundled artifact (no API key, no agent run, ~30 s):

```bash
uv run python -m eval.replay eval/sample_artifact \
    --image shaswatpatel123/lea-minif2f:v4.24.0 --project-root /work/minif2f
# → RESULT: PASS
```

### Why version tags, not `:latest`

Each image is tagged both `:v<toolchain>` (e.g. `:v4.28.0`) and `:latest`. **Pin to the version tag** when you produce evaluation results — captured artifacts only reproduce against the exact image they were created with, and Mathlib drift across toolchain bumps can change verify outcomes. `:latest` is fine for ad-hoc experimentation.

### What's inside each image

- Ubuntu 24.04 + elan + the benchmark's pinned Lean toolchain
- Lake project metadata (`lakefile.{lean,toml}`, `lake-manifest.json`, `lean-toolchain`)
- Built `.lake/` (Mathlib + every package dep, populated via `lake exe cache get`, ~6–8 GB)
- An empty `testbed/` directory under the project root

The image does NOT bake problem `.lean` files. The runner injects `testbed/Main.lean` per attempt.

### Building from source

To rebuild an image locally (e.g. when a benchmark's toolchain bumps): see `docker/README.md` for the per-image Dockerfile, sanity tests, and the build-and-push workflow.

## Adding a new benchmark

The pattern is two files:

1. **`docker/<bench>.Dockerfile`** — copy `docker/minif2f.Dockerfile` (the cleanest current example) and swap:
   - `--default-toolchain leanprover/lean4:<version>` (read from the benchmark's `lean-toolchain`)
   - `COPY` lines for the benchmark's Lake metadata
   - `WORKDIR /work/<bench>`

2. **`eval/run_<bench>_parallel.py`** — copy `eval/run_minif2f_parallel.py` and swap the four constants at the top:
   ```python
   <BENCH>_DIR = REPO_ROOT / "<bench-source-dir>"
   PROBLEMS_BASE = <BENCH>_DIR / "<problems-subdir>"
   DEFAULT_IMAGE = "shaswatpatel123/lea-<bench>:<toolchain>"
   PROJECT_ROOT_IN_CONTAINER = "/work/<bench>"
   ```

If the benchmark's problem layout differs (e.g. `<dir>/Main.lean` like FQB vs flat `.lean` like miniF2F), tweak `discover_problems()` accordingly.

## Existing serial runners

`run_fqb.py`, `run_minif2f.py`, `run_putnam.py`, `run_baseline.py`, `run_fqb_best_of_n.py` all still work. They now route through `lea.env.local.LocalEnvironment(<project_dir>)` so the env layer is consistent across modes, but they remain single-threaded and operate directly on the host filesystem.

When to use which:
- **Parallel** — actual eval runs, contamination-sensitive work, anything that needs replayability.
- **Serial** — quick iteration on a single problem, debugging the agent's behavior, or when Docker is unavailable.

`run_baseline.py` is a single-API-call non-agent baseline; it doesn't use the env layer.

## Verifying the implementation

The test suite (88 checks across 4 files, all run via `python -m tests.<name>`):

```bash
uv run python -m tests.env.test_local         # 15 — LocalEnvironment, no Docker
uv run python -m tests.env.test_docker        # 25 — DockerEnvironment, SKIP if daemon down
uv run python -m tests.tools.test_tools       # 38 — all 6 tools via build_handlers(env)
uv run python -m tests.runner.test_harness    # 10 — per-attempt orchestration + replay end-to-end
```

`tests/runner/test_harness.py` is the most load-bearing of these: it spins a real container, simulates an agent editing both `testbed/Main.lean` and a Mathlib file, captures, then runs replay from scratch in a second container and asserts the replayed file content matches byte-for-byte AND `lake env lean` passes. That's the proof that the artifact contract holds.

## Troubleshooting

**`KeyError: 'GOOGLE_API_KEY'` (or `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).**
Set the variable in the shell where you launch the runner. The exact variable names are what the provider SDKs read.

**`docker: error during connect: ... daemon: not running`.**
Start Docker Desktop on macOS or `sudo systemctl start docker` on Linux. The parallel runner needs the daemon.

**Image pull is slow.**
Each image is ~13–15 GB. First pull is once-per-machine. Subsequent runs reuse the local layer cache.

**Out of memory during compile.**
Drop `--workers`. Each active container takes ~3–4 GB of RAM during `lake env lean`. On a 32 GB Mac, 2–3 workers is comfortable; 4+ starts swapping.

**Replay shows different result than the original.**
The artifact carries `head_at_snap` sha per repo. If the image's pinned Mathlib has changed between original run and replay, replay can disagree. The fix is to replay against the exact same image tag (e.g. `:v4.28.0`, not `:latest`). The bundled sample artifact pins miniF2F v4.24.0 explicitly.
