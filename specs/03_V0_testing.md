# V0 Manual Setup and Testing Guide

## Purpose

This guide takes a first-time user from a fresh checkout to a reviewed V0
candidate patch. It covers:

- installing and checking prerequisites;
- configuring the host-side OpenAI runtime;
- building and smoke-testing the Docker sandbox;
- preparing a committed Git repository and a useful issue;
- running a live issue solve;
- inspecting and testing the generated candidate;
- understanding expected output and exit codes; and
- recovering from common setup and runtime failures.

The instructions match the implementation described in
[`V0_IMPLEMENTATION.md`](02_V0_implementation.md). Commands are run from the root
of the Sage repository unless a step explicitly says otherwise.

V0.1 replaces the internal Agents SDK loop with the project-owned LangGraph
runtime without changing this issue-to-patch workflow. See
[`06_V0.1_testing.md`](06_V0.1_testing.md) for migration-specific checks and the
new all-in-one setup-and-solve command.

All commands and paths in this guide use the current Sage identifiers.

> A live solve sends the issue and selected repository context to the configured
> OpenAI model and may incur API usage charges. The deterministic test suite and
> sandbox smoke test do not call the model.

## What V0 does—and does not do

V0 accepts a local Git repository, a Markdown or text issue, and a committed
base reference. It then:

1. resolves the base reference to a commit;
2. creates a separate clone under `.sage/runs/`;
3. starts a temporary, network-disabled Docker container over that clone;
4. lets one coding agent inspect, edit, and test through bounded tools;
5. saves the candidate checkout, full Git patch, changed-file list, and run
   metadata; and
6. removes the temporary container.

V0 does **not** modify the source checkout, include its uncommitted changes,
commit the candidate, push a branch, read a GitHub issue, or open a pull request.
The user reviews and applies the result manually.

The main data flow is:

```text
committed source repository + issue file
                  |
                  v
          host-side controller
                  |
                  v
       isolated clone in run directory
                  |
                  v
     temporary Docker sandbox, no network
                  |
                  v
        candidate repo + diff.patch
```

## Fast path

Experienced users can use this checklist. New users should continue to the
detailed sections.

```bash
# From the Sage repository root
make env
# Edit .env and set OPENAI_API_KEY.

make bootstrap
make check

make new-issue ISSUE=/absolute/path/to/my-issue.md
# Edit the new issue file.

make solve \
  REPO=/absolute/path/to/a/committed/git/repository \
  ISSUE=/absolute/path/to/my-issue.md

# Copy the absolute run directory printed by the solve.
make run-status RUN_DIR=/absolute/path/to/.sage/runs/<run-id>
make run-test \
  RUN_DIR=/absolute/path/to/.sage/runs/<run-id> \
  TEST_COMMAND="python3 -m unittest discover -v"
```

After a committed target repository and issue file exist, the V0.1 all-in-one
alternative is:

```bash
make first-run REPO=/absolute/path/to/repository ISSUE=/absolute/path/to/issue.md
```

Do not use a real repository for the first test if you are unsure how its build
works. The [reproducible first run](#7-reproducible-first-live-run) below creates
a small throwaway repository with no third-party dependencies.

## 1. Prerequisites

### Required for the backend

| Requirement | Why it is needed | Quick check |
| --- | --- | --- |
| Git | Resolves the base commit, clones the source, and creates the patch. | `git --version` |
| uv | Installs the locked Python environment and runs the CLI/tests. | `uv --version` |
| Python 3.14 | Required by `apps/agent/pyproject.toml`; uv can manage it. | `uv python find 3.14` |
| Docker Engine or Docker Desktop | Runs repository commands in the isolated sandbox. | `docker --version` |
| Reachable Docker daemon | Required to inspect, create, and remove containers. | `docker info` |
| OpenAI API key | Used by the host-side agent runtime for live solves only. | Configured later; never print it. |
| Bash | Runs Make recipes and loads the optional `.env` file. | `bash --version` |
| GNU Make | Recommended wrapper for this guide. Raw commands are also provided. | `make --version` |

Node.js and npm are only needed for development of the landing page. They are
not needed to set up or manually test the V0 backend.

The easiest environments are Linux, macOS with Docker Desktop, or Windows
through WSL2 with Docker Desktop integration enabled. On Docker Desktop, the
configured `SAGE_RUNS_DIR` must be in a location Docker is allowed to
bind-mount.

If uv is installed but Python 3.14 is not, run:

```bash
uv python install 3.14
```

Then repeat `uv python find 3.14`.

### Verify the checkout

Enter the repository root—the directory containing `Makefile`, `apps/`, and
`docker/`—and save its location for the walkthrough:

```bash
cd /absolute/path/to/sage
export SAGE_ROOT="$PWD"
git status --short
make help
```

`git status --short` may show your own local work. Sage does not require
its controller checkout to be clean, but you should understand those changes
before doing development work. Never put an API key in a tracked file.

## 2. Configure the controller

### Recommended: local `.env` file used by the Makefile

Create the ignored local configuration file:

```bash
make env
```

The command copies `.env.example` to `.env` and refuses to overwrite an
existing file. Open `.env` in a text editor and set at least:

```dotenv
OPENAI_API_KEY=replace-with-your-real-key
OPENAI_MODEL=gpt-5.4-mini
```

Use a model that is available to the API project associated with the key. The
shown model is the V0 default, not a guarantee that every API project can use
it. If the provider reports that the model does not exist or is inaccessible,
set `OPENAI_MODEL` to an enabled model appropriate for coding-agent tool use.

Keep the remaining defaults for the first run:

```dotenv
SAGE_MAX_TURNS=30
SAGE_RUNS_DIR=.sage/runs
SAGE_SANDBOX_IMAGE=sage-sandbox:v0
SAGE_COMMAND_TIMEOUT_SECONDS=60
SAGE_MAX_TOOL_OUTPUT_CHARS=12000
```

Optional local permission hardening:

```bash
chmod 600 .env
```

The Python application deliberately has no dotenv dependency. The Makefile
sources `.env` before commands that need configuration. If you bypass Make,
you must export or source the values yourself.

`.env` uses Bash shell syntax. Avoid spaces around `=`. Quoted values are
allowed. Do not paste commands or untrusted content into this file because it
is sourced by the shell.

### Alternative: export values only in the current shell

```bash
export OPENAI_API_KEY="replace-with-your-real-key"
export OPENAI_MODEL="gpt-5.4-mini"
```

If a `.env` file exists, Makefile commands source it after inheriting the shell
environment. Keep one configuration method per terminal session to avoid an
empty or stale `.env` value replacing an exported value. To intentionally use a
different file, pass `ENV_FILE=path/to/file.env` to Make.

## 3. Install and verify the project

### One-command setup

After configuring the API key, run:

```bash
make bootstrap
```

This performs four operations in order:

1. `uv sync` installs the locked backend environment;
2. Docker builds `sage-sandbox:v0`;
3. a disposable, network-disabled container verifies Git, Python, and ripgrep;
4. the doctor checks tools, Docker, the image, the virtual environment, and the
   presence of the API key.

The initial dependency and image downloads require host network access and may
take several minutes. The repository sandbox itself has no network when a solve
is running.

A successful doctor ends with `OK` lines for:

- `git`, `uv`, and `docker`;
- the Docker daemon;
- the selected sandbox image;
- `OPENAI_API_KEY` (the value is hidden); and
- the Python 3.14 backend environment.

You can rerun only the relevant setup stage:

```bash
make setup
make sandbox-build
make sandbox-smoke
make doctor
```

### Run deterministic verification

Before spending money on a live solve, run:

```bash
make check
```

This runs the unit suite and Python bytecode compilation. The unit tests fake
the Docker and model boundaries, so they do not make paid model calls.

Expected result:

```text
68 passed
```

The exact timing and pytest progress formatting can vary. A different test
count after future development is acceptable; failures are not.

### Raw setup commands without Make

If GNU Make is unavailable, the equivalent commands are:

```bash
uv sync --project apps/agent

docker build \
  --tag sage-sandbox:v0 \
  --file docker/sandbox/Dockerfile \
  .

set -a
source .env
set +a

uv run --project apps/agent pytest
uv run --project apps/agent python -m compileall -q apps/agent/src
uv run --project apps/agent sage --help
```

## V0.1 runtime verification

These checks prove the internal runtime was migrated without making a live
model call.

Confirm the removed Agents SDK is absent:

```bash
uv tree --project apps/agent | rg "openai-agents"
```

Expected: no output and `rg` exit code `1`.

Confirm the new runtime packages are present:

```bash
uv tree --project apps/agent | rg "langgraph|langchain-openai"
```

Expected: both `langgraph` and `langchain-openai` appear.

Print the topology from the compiled executable graph:

```bash
make graph
```

Expected: Mermaid text containing `agent`, `tools`, `finalize`, `turn_limit`,
and `invalid_response`. It must show `tools --> agent` and terminal edges to
`__end__`. This target uses a fake model and makes no API call.

Run the focused runtime tests when diagnosing graph behavior:

```bash
uv run --project apps/agent pytest apps/agent/tests/runtimes
```

Expected: 42 passing runtime tests. `make check` runs these together with all
existing repository, workflow, sandbox, configuration, and artifact tests.

## 4. Understand the sandbox

You do not manually create a long-running sandbox for a solve. The controller
does this automatically:

1. it creates the isolated candidate clone on the host;
2. it starts `sage-<run-id>` with that clone mounted at `/workspace`;
3. the model invokes repository commands inside the container;
4. it captures Git results; and
5. it force-removes the container in a cleanup path.

The default image is Ubuntu 24.04 with Bash, Git, Python 3, ripgrep, core/file
utilities, and CA certificates. The running container has:

- no network;
- no OpenAI key or host environment dump;
- no Docker socket;
- one writable bind mount: the isolated clone at `/workspace`;
- dropped Linux capabilities and `no-new-privileges`;
- bounded CPU, memory, process count, and command time; and
- `HOME=/tmp`.

Run the sandbox-only smoke test at any time:

```bash
make sandbox-smoke
```

This starts a separate disposable container, prints the versions of the three
required in-container tools, and removes the container. It does not mount a
repository or call a model.

### When a custom sandbox is required

The default image can run standard-library Python tests, but it cannot run
Node, Java, Go, Rust, a database, or third-party Python packages unless those
tools are added to an image. Because solve-time networking is disabled,
dependency installation must happen while building the image, not during the
agent run.

Create a project-specific Dockerfile that extends the default image, install
only the required runtime and dependencies, and build it explicitly. For
example:

```dockerfile
FROM sage-sandbox:v0

RUN apt-get update \
    && apt-get install -y --no-install-recommends <required-packages> \
    && rm -rf /var/lib/apt/lists/*
```

```bash
docker build \
  --tag my-project-sandbox:v0 \
  --file /absolute/path/to/Dockerfile.sage \
  /path/to/its/build-context

make solve \
  REPO=/absolute/path/to/repo \
  ISSUE=/absolute/path/to/issue.md \
  SANDBOX_IMAGE=my-project-sandbox:v0
```

`make sandbox-build` always uses this repository's default
`docker/sandbox/Dockerfile`. Passing `SANDBOX_IMAGE` to that target changes the
tag, not the Dockerfile contents.

## 5. Prepare a target repository

**Recommended plan:** keep every test target repository outside the Sage
directory. This avoids nested `.git` directories, prevents the controller and
target histories from being confused, and makes cleanup straightforward.

### 5.1 Know which repository is which

There are three different repository locations during a V0 solve. Keeping their
names separate prevents most path-related mistakes:

| Name | Example host path | Purpose | Modified by V0? |
| --- | --- | --- | --- |
| Controller repository | `/home/user/sage` | Contains Sage, the Makefile, and Dockerfile. | No, unless it is deliberately also the target. |
| Target/source repository | `/home/user/projects/sage-manual-tests/repos/my-app` | The external Git repository whose issue should be solved. | No. V0 only reads and clones a committed revision. |
| Run/candidate repository | `sage/.sage/runs/<run-id>/repo` | The isolated clone in which the candidate change is made. | Yes. This is the writable candidate. |

`REPO=...` always means the **target/source repository**. It does not mean the
Sage controller repository unless the issue actually concerns Sage
itself.

The target must be a local Git working tree with at least one commit. It does
not need a GitHub remote, and its location does not need to be copied into the
Docker image.

### 5.2 Create a dedicated external testing workspace

Choose an absolute directory that is outside `/absolute/path/to/sage`.
The guide uses this layout:

```text
/home/user/projects/
  sage/                         <- controller repository
  sage-manual-tests/            <- external test workspace
    repos/
      refactor-demo-.../              <- target Git repository
    issues/
      refactor-demo-....md            <- issue input
```

First save the controller location:

```bash
cd /absolute/path/to/sage
export SAGE_ROOT="$PWD"
```

Then create a sibling workspace next to Sage:

```bash
export TEST_WORKSPACE_ROOT="$(dirname "$SAGE_ROOT")/sage-manual-tests"

mkdir -p "$TEST_WORKSPACE_ROOT/repos"
mkdir -p "$TEST_WORKSPACE_ROOT/issues"
```

Confirm the two roots are different and neither contains the other:

```bash
printf 'Controller: %s\n' "$SAGE_ROOT"
printf 'Test data:  %s\n' "$TEST_WORKSPACE_ROOT"
```

For example, if Sage is at `/home/user/projects/sage`, this creates
`/home/user/projects/sage-manual-tests`. You may select another absolute
path you own, but do not choose a directory inside Sage, including its
`.sage/` directory.

### 5.3 Create the external target repository

Create a uniquely named repository. The timestamp avoids overwriting a fixture
from an earlier attempt:

```bash
export TARGET_REPO="$TEST_WORKSPACE_ROOT/repos/refactor-demo-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$TARGET_REPO"

git -C "$TARGET_REPO" init
```

Now create the project files that should exist before the refactor. For example,
copy or write the source code and tests into `$TARGET_REPO`, then make the
initial commit:

```bash
git -C "$TARGET_REPO" add .
git -C "$TARGET_REPO" \
  -c user.name="Sage Manual Test" \
  -c user.email="sage-test@example.invalid" \
  commit -m "test: create refactor fixture"
```

The `-c` values apply only to this commit; they do not change global Git
configuration. The fully worked calculator example in
[section 7](#7-reproducible-first-live-run) provides exact source and test
files if you do not already have a fixture.

### 5.4 Prove that `REPO` resolves to the external repository

Run these checks before a paid API call:

```bash
export TARGET_TOPLEVEL="$(git -C "$TARGET_REPO" rev-parse --show-toplevel)"
export TARGET_RESOLVED="$(cd "$TARGET_REPO" && pwd -P)"

printf 'Requested target: %s\n' "$TARGET_RESOLVED"
printf 'Git top-level:    %s\n' "$TARGET_TOPLEVEL"

test "$TARGET_TOPLEVEL" = "$TARGET_RESOLVED"
git -C "$TARGET_REPO" status --short
git -C "$TARGET_REPO" log -1 --oneline
git -C "$TARGET_REPO" rev-parse --verify HEAD
```

Expected result:

- `Requested target` and `Git top-level` are identical;
- `test` exits silently with code `0`;
- the log displays the fixture commit; and
- status is empty unless you intentionally made another local change.

If `Git top-level` and `Requested target` differ, stop. The target path is wrong
or the directory is not an independent repository. Move to the external test
workspace, run `git init`, and create at least one commit there before
continuing.

### 5.5 Rules for an existing target repository

For a real repository elsewhere on disk, set its absolute path and perform the
same checks:

```bash
export TARGET_REPO=/absolute/path/to/existing-repository

git -C "$TARGET_REPO" rev-parse --show-toplevel
git -C "$TARGET_REPO" status --short
git -C "$TARGET_REPO" log -1 --oneline
git -C "$TARGET_REPO" rev-parse --verify HEAD
```

Important rules:

- The default base is `HEAD`.
- `BASE_REF` may be a commit SHA, local branch, or tag that resolves locally.
- Only the selected committed revision is cloned.
- Uncommitted and untracked files in the source checkout are intentionally
  absent from the candidate clone.
- The source remote does not need to be GitHub; V0 only needs the local repo.
- Prefer an absolute path to avoid confusion about the current directory.

If the issue depends on local changes, commit them on an appropriate branch
first or select a different already-committed base. Do not commit secrets merely
to make them visible to the agent.

### 5.6 Exception: solving an issue in Sage itself

Use the controller root as `REPO` only if the issue is specifically intended to
change Sage itself. This is not the layout for an unrelated test project:

```bash
export TARGET_REPO="$SAGE_ROOT"
export BASE_SHA="$(git -C "$TARGET_REPO" rev-parse HEAD)"

make solve \
  REPO="$TARGET_REPO" \
  ISSUE="$ISSUE_FILE" \
  BASE_REF="$BASE_SHA"
```

V0 still clones the selected Sage commit and edits the run clone; it does
not edit the controller checkout. Any uncommitted controller changes—including
new specs, Makefile changes, or local test fixtures—will not appear in that
clone. Commit the required non-secret inputs first or choose an existing commit
that already contains them.

Do not run `git init` in the Sage root again. It is already a repository.

### 5.7 How the target reaches the Docker sandbox

The source repository is not copied into the Docker image, and the original
source checkout is never mounted into the container. V0 performs this sequence
on every solve:

1. The CLI converts `REPO` to an absolute path.
2. Host-side Git resolves `BASE_REF` to a commit SHA.
3. With the default `SAGE_RUNS_DIR`, host-side Git runs an isolated clone
   equivalent to:

   ```bash
   git clone --no-hardlinks --no-checkout \
     "$TARGET_REPO" \
     "$SAGE_ROOT/.sage/runs/<run-id>/repo"
   ```

4. Host-side Git checks out the resolved SHA in detached-HEAD mode inside the
   run clone.
5. Docker starts a temporary container and bind-mounts only that run clone:

   ```text
   host:      <run-directory>/repo
                         |
                         | Docker bind mount
                         v
   container: /workspace
   ```

6. Every repository tool and test command runs with `/workspace` as its working
   directory. Changes made under `/workspace` are immediately reflected in the
   host-side run clone because it is a bind mount.
7. V0 derives `diff.patch` and `changed-files.json` from the run clone, then
   removes the container. The run directory remains for review.

With the recommended external layout, the complete flow looks like:

```text
external test workspace/
  repos/
    refactor-demo-.../          <- REPO; original committed source, unchanged
      .git/
      ...
  issues/
    refactor-demo-....md        <- ISSUE; read by the host controller

sage/                     <- controller repository
  .sage/
    runs/
      <run-id>/
        issue.md                <- saved copy of the issue
        diff.patch
        repo/                   <- isolated candidate clone on the host
          .git/
          ...
             |
             +----------------- bind-mounted to /workspace in Docker
```

The issue file itself does not need to be mounted. The host controller reads
it, includes its text in the model input, and saves a copy as a run artifact.
The OpenAI API key also stays on the host and is not passed to the repository
container.

No `docker cp`, Git remote, GitHub account, or network access inside the
container is needed. On Docker Desktop, the path containing
`SAGE_RUNS_DIR` must be allowed for file sharing because that generated
clone is the path Docker mounts.

### 5.8 Observe the mount during a live run

The container exists only while the agent is running. From a second terminal,
you can inspect it without changing anything:

```bash
docker ps --filter name=sage-
```

Copy the exact container name from that output, then run:

```bash
docker inspect sage-<exact-run-id> \
  --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
```

Expected output contains one mount ending in:

```text
.../.sage/runs/<run-id>/repo -> /workspace
```

The container may finish before you inspect it; that is normal. After cleanup,
use the `Workspace` and `Patch` paths printed by the CLI to inspect the same
candidate on the host.

## 6. Write a useful issue

The issue is a normal UTF-8 Markdown or text file. It may live anywhere the host
controller can read it. It does not need to be inside the target repository or
committed to Git, because the host reads it separately before the sandbox
starts.

For the external testing layout, create it beside the test repositories:
```bash
export ISSUE_FILE="$TEST_WORKSPACE_ROOT/issues/refactor-demo.md"
make new-issue ISSUE="$ISSUE_FILE"
```

For a repository stored elsewhere, any absolute path works:

```bash
make new-issue ISSUE=/absolute/path/to/my-issue.md
```

In both forms, the target issue file must not already exist; the command refuses
to overwrite it. Edit the file so it contains enough information to determine
when the work is done. A useful issue includes:

- the observed incorrect or missing behavior;
- the expected behavior;
- a small reproduction or exact affected component;
- constraints and behavior that must remain unchanged; and
- the test command or acceptance criteria when known.

Example:

```markdown
# Fix addition for positive and negative integers

`calculator.add(a, b)` currently subtracts `b`. Change it so it returns the
mathematical sum for positive and negative integers.

## Acceptance criteria

- `add(2, 3)` returns `5`.
- `add(-1, 1)` returns `0`.
- Keep the public function name and arguments unchanged.
- Run `python3 -m unittest discover -v`.
```

Do not put API keys, credentials, private customer data, or irrelevant sensitive
content in the issue or target repository.

## 7. Reproducible first live run

This exercise deliberately starts with a failing standard-library Python test.
It lets you validate the complete issue-to-patch flow without changing a real
project or installing project dependencies.

### 7.1 Create a throwaway committed repository

Run these commands in the same terminal used for setup. This creates the target
in the separate external testing workspace:

```bash
cd "$SAGE_ROOT"

export DEMO_REPO="$TEST_WORKSPACE_ROOT/repos/calculator-demo-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEMO_REPO"

cat > "$DEMO_REPO/calculator.py" <<'PY'
def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a - b
PY

cat > "$DEMO_REPO/test_calculator.py" <<'PY'
import unittest

from calculator import add


class AddTests(unittest.TestCase):
    def test_positive_integers(self) -> None:
        self.assertEqual(add(2, 3), 5)

    def test_negative_and_positive_integers(self) -> None:
        self.assertEqual(add(-1, 1), 0)


if __name__ == "__main__":
    unittest.main()
PY

git -C "$DEMO_REPO" init
git -C "$DEMO_REPO" add calculator.py test_calculator.py
git -C "$DEMO_REPO" \
  -c user.name="Sage Manual Test" \
  -c user.email="sage-test@example.invalid" \
  commit -m "test: add broken calculator fixture"

git -C "$DEMO_REPO" status --short
git -C "$DEMO_REPO" log -1 --oneline
git -C "$DEMO_REPO" rev-parse --show-toplevel
```

Expected state:

- the commit succeeds;
- the log shows `test: add broken calculator fixture`;
- `git status --short` prints nothing because the source checkout is clean; and
- `git -C "$DEMO_REPO" rev-parse --show-toplevel` prints the same path stored in
  `$DEMO_REPO`, not the Sage root.

The bug is intentional: `add(2, 3)` currently returns `-1`.

### 7.2 Create the issue file

```bash
export ISSUE_FILE="$TEST_WORKSPACE_ROOT/issues/$(basename "$DEMO_REPO").md"
mkdir -p "$(dirname "$ISSUE_FILE")"

cat > "$ISSUE_FILE" <<'MARKDOWN'
# Fix addition for positive and negative integers

`calculator.add(a, b)` currently subtracts `b`. Change it so it returns the
mathematical sum for positive and negative integers.

## Acceptance criteria

- `add(2, 3)` returns `5`.
- `add(-1, 1)` returns `0`.
- Keep the public function name and arguments unchanged.
- Run `python3 -m unittest discover -v`.
MARKDOWN

sed -n '1,160p' "$ISSUE_FILE"
```

### 7.3 Run the solve

Return to the Sage root and run the live workflow:

```bash
cd "$SAGE_ROOT"
make doctor

make solve \
  REPO="$DEMO_REPO" \
  ISSUE="$ISSUE_FILE"
```

The call can take several minutes. Normal logs include workspace preparation,
artifact initialization, sandbox start, repository tool calls, and sandbox
cleanup. Tool-call count and ordering are model decisions and can vary.

A successful changed result prints output shaped like:

```text
Sage V0

Run: <timestamp-and-random-id>
Base: <12-character-SHA>
Model: <configured-model>

Changed files:
  calculator.py

Summary:
  <model summary>

Workspace:
  /.../.sage/runs/<run-id>/repo

Patch:
  /.../.sage/runs/<run-id>/diff.patch
```

Copy the **run directory**, which is the patch path without `/diff.patch`, into
an absolute shell variable:

```bash
export RUN_DIR=/absolute/path/to/.sage/runs/<run-id>
```

Do not literally keep `<run-id>` or the example path. Use the path printed by
your invocation.

### 7.4 Inspect the authoritative result

```bash
make run-status RUN_DIR="$RUN_DIR"

sed -n '1,240p' "$RUN_DIR/diff.patch"
sed -n '1,200p' "$RUN_DIR/agent-final.json"
git -C "$RUN_DIR/repo" diff
git -C "$RUN_DIR/repo" branch --show-current
git -C "$RUN_DIR/repo" rev-parse HEAD
```

For this exercise, confirm all of the following:

- `changed-files.json` names `calculator.py` and no unrelated file;
- the patch changes `return a - b` to `return a + b` or an equally correct
  minimal implementation;
- `git diff --check` passes;
- the candidate checkout is detached at the base commit; and
- the summary matches the actual diff.

The model's `changed_files_claimed` field in `agent-final.json` is informative.
`changed-files.json` and `diff.patch`, both derived from Git, are authoritative.

### 7.5 Test the candidate in a fresh sandbox

```bash
make run-test \
  RUN_DIR="$RUN_DIR" \
  TEST_COMMAND="python3 -m unittest discover -v"
```

Expected result: two passing tests. This test uses another network-disabled
container over the completed candidate checkout; it does not call the model.

### 7.6 Confirm source-checkout isolation

```bash
git -C "$DEMO_REPO" status --short
sed -n '1,40p' "$DEMO_REPO/calculator.py"
sed -n '1,40p' "$RUN_DIR/repo/calculator.py"
```

Expected result:

- the original repository remains clean;
- the original still contains `return a - b`; and
- only the run checkout contains the candidate fix.

This is a core V0 acceptance criterion. A changed original source checkout is a
failure and should be investigated before using V0 on real work.

## 8. Run against a real local issue

After the throwaway test passes:

```bash
make solve \
  REPO=/absolute/path/to/real/repository \
  ISSUE=/absolute/path/to/real/issue.md \
  BASE_REF=HEAD
```

Use an explicit commit SHA for the most reproducible run:

```bash
BASE_SHA="$(git -C /absolute/path/to/real/repository rev-parse HEAD)"

make solve \
  REPO=/absolute/path/to/real/repository \
  ISSUE=/absolute/path/to/real/issue.md \
  BASE_REF="$BASE_SHA"
```

If the normal error is too concise, rerun with tracebacks and debug logs:

```bash
make solve-debug \
  REPO=/absolute/path/to/real/repository \
  ISSUE=/absolute/path/to/real/issue.md \
  BASE_REF="$BASE_SHA"
```

A retry creates a new run directory. It does not resume or overwrite the old
run.

## 9. Review and optionally apply a candidate patch

Every completed run has this layout:

```text
.sage/runs/<run-id>/
  request.json          normalized input paths and selected options
  metadata.json         run ID, time, base ref/SHA, model, image
  issue.md              exact issue sent to the runtime
  agent-final.json      model summary, claimed files, uncertainty
  changed-files.json    Git-derived changed paths
  diff.patch            complete binary-capable Git patch
  repo/                 inspectable candidate checkout
```

Review `diff.patch`, run the target project's tests, and inspect all changed
files before applying anything. On a clean target checkout at the same base,
first check whether the patch applies:

```bash
git -C /absolute/path/to/target apply --check "$RUN_DIR/diff.patch"
```

If you choose to accept it, create a branch in the target repository and apply
the patch yourself:

```bash
git -C /absolute/path/to/target switch -c sage/manual-review
git -C /absolute/path/to/target apply "$RUN_DIR/diff.patch"
git -C /absolute/path/to/target status --short
git -C /absolute/path/to/target diff
```

Those commands intentionally mutate the target checkout. Do not run them until
you have selected the correct target, confirmed it is clean, and reviewed the
patch. V0 itself never performs this step.

## 10. Exit codes and Make behavior

The raw `sage solve` command uses:

| Exit code | Meaning |
| --- | --- |
| `0` | The runtime completed and Git found a non-empty candidate diff. |
| `1` | Configuration, repository, sandbox, runtime, artifact, or unexpected failure. |
| `2` | The runtime completed normally but produced no repository change. |

GNU Make normally treats every non-zero code as a failed target. To avoid a
confusing `make: ... Error 2` after an otherwise successful no-change run,
`make solve` and `make solve-debug` print a warning and translate CLI code `2`
to Make success. They still propagate real failures as non-zero.

If a script needs to distinguish `0`, `1`, and `2`, use the raw CLI:

```bash
set -a
source .env
set +a

uv run --project apps/agent sage solve \
  --repo /absolute/path/to/repository \
  --issue-file /absolute/path/to/issue.md \
  --base-ref HEAD

echo "$?"
```

## 11. Manual acceptance test matrix

Run the first five checks before declaring a local V0 installation usable.
The remaining failure-path checks are useful for release testing.

| ID | Test | Procedure | Expected result |
| --- | --- | --- | --- |
| MT-01 | Prerequisite validation | Run `make doctor`. | Every line is `OK`; the key value is never printed. |
| MT-02 | Deterministic backend | Run `make check`. | Tests and compilation succeed without a model call. |
| MT-03 | Sandbox smoke | Run `make sandbox-smoke`. | Git, Python, and ripgrep versions print; the container exits and is removed. |
| MT-04 | End-to-end changed solve | Complete the throwaway calculator run. | CLI reports `calculator.py`, patch exists, tests pass. |
| MT-05 | Source isolation | Compare the original and run checkout after MT-04. | Original is clean and unchanged; candidate contains the fix. |
| MT-06 | Artifact integrity | Run `make run-status RUN_DIR=...`. | Six result files and candidate repo exist; `git diff --check` passes. |
| MT-07 | Missing issue | Run a raw solve with a nonexistent issue path. | Exit `1`; error identifies the missing issue before a model call. |
| MT-08 | Invalid repository | Run a raw solve with a non-Git directory. | Exit `1`; error states that the path is not a Git repository. |
| MT-09 | Invalid base | Pass `BASE_REF=does-not-exist`. | Exit `1`; error states that the base does not resolve to a commit. |
| MT-10 | Missing image | Pass `SANDBOX_IMAGE=missing:image`. | Exit `1`; error identifies the missing sandbox image before a model call. |
| MT-11 | No-change issue | Ask only for analysis with an explicit instruction not to edit. | Raw CLI exits `2`; artifacts show an empty authoritative diff. Model behavior can vary, so do not use this as a deterministic test. |
| MT-12 | Cleanup | After a solve, run `docker ps --filter name=sage-`. | No container from the completed run remains. |

Failure-path live solves can still consume API usage if validation passes and
the model starts. Prefer the deterministic preflight cases when testing error
handling.

## 12. Troubleshooting

### `uv: command not found`

Install uv, open a new terminal if its installer changed `PATH`, and verify:

```bash
uv --version
make setup
```

### Python 3.14 cannot be found

```bash
uv python install 3.14
uv python find 3.14
make setup
```

If downloads are blocked by a proxy or firewall, configure host network access
for uv. The solve container's lack of network is unrelated to setup downloads.

### `Docker daemon is not reachable`

`docker --version` only proves the client is installed. Start Docker Engine or
Docker Desktop, then run:

```bash
docker info
make doctor
```

On Linux, also confirm the current user is authorized to use the Docker daemon.
Do not work around a permission problem by running the entire Sage
controller as root unless that is an intentional, reviewed environment policy.

### `Docker sandbox image does not exist`

Build the image selected by `.env`:

```bash
make sandbox-build
make sandbox-smoke
```

If the solve passes `SANDBOX_IMAGE=...`, build that exact tag or remove the
override.

### `OPENAI_API_KEY is required` or the Makefile says it is empty

Open `.env`, set a non-empty value, and run `make doctor`. The file must be valid
Bash assignment syntax. The Makefile does not print the secret.

If you exported the key in the shell but also have a stale `.env`, update or
remove the stale file, or deliberately select another configuration file with
`ENV_FILE=...`.

### Authentication, quota, rate-limit, or model-access error

The infrastructure checks can succeed even when the provider rejects a live
request. Verify that:

- the key belongs to the intended API project;
- API billing/quota is available;
- `OPENAI_MODEL` is enabled for that project; and
- the host can reach the provider API.

Then rerun with `make solve-debug ...` for the underlying provider error. Never
paste the key into an issue, log, or bug report.

### `Path is not a Git repository`

Point `REPO` at the working tree rather than its parent directory. Verify with:

```bash
git -C /absolute/path/to/repository rev-parse --show-toplevel
```

### `Base ref does not resolve to a commit`

The ref must exist in the local source checkout. Check it directly:

```bash
git -C /absolute/path/to/repository \
  rev-parse --verify --end-of-options 'HEAD^{commit}'
```

Replace `HEAD` with the same ref passed to the solve. Fetching a remote branch
is a separate host-side action; the network-disabled sandbox cannot fetch it.

### The agent cannot see a file or recent change

V0 clones the selected committed revision. It never copies source-checkout
uncommitted or untracked changes. Run `git status --short`, commit the required
non-secret files, and select that commit as `BASE_REF`.

### Tests fail because a command or dependency is missing

The default sandbox is deliberately small, and commands cannot download
dependencies during a solve. Build a custom sandbox with the required runtime
and dependencies preinstalled, smoke-test it, and pass its image tag to the
solve. Keep additions minimal so the sandbox remains reviewable.

### Tests try to access the internet

Network failure is expected inside the sandbox. Unit tests should mock external
services. If the repository fundamentally requires a live service or download,
V0 cannot execute that part under its current isolation policy. Record the
limitation during review rather than weakening isolation casually.

### Repository mount is denied or empty

On Docker Desktop, ensure `SAGE_RUNS_DIR` (the directory containing the
generated candidate clone) is available for file sharing. The original target
source is read and cloned by the host controller; Docker mounts only the run
checkout. Prefer ordinary local filesystem paths rather than network drives,
virtual filesystem paths, or directories with restrictive mount policies.

### The run times out

Repository commands are limited by `SAGE_COMMAND_TIMEOUT_SECONDS`, and
the overall agent loop is bounded by `SAGE_MAX_TURNS`. First determine
whether the target test command is genuinely slow or blocked. If a larger
bounded value is justified, change `.env`, for example:

```dotenv
SAGE_COMMAND_TIMEOUT_SECONDS=120
SAGE_MAX_TURNS=40
```

Do not make limits unbounded. A deterministic validation error should be fixed,
not retried with more turns.

### `Model turn limit (...) was reached with an unexecuted tool call`

V0.1 counts only executions of the `agent` graph node. If the model requests a
tool on its final allowed turn, the graph refuses to execute it because no turn
would remain to consume the result. First confirm the issue is focused and the
repository is inspectable. If the task reasonably needs more decisions, raise
`SAGE_MAX_TURNS` by a bounded amount and rerun. A retry creates a new run;
it does not resume the failed graph.

Use `make graph` to confirm the `agent -> tools -> agent` loop and use
`make solve-debug ...` for the application traceback. The API key, complete
issue, file contents, and patches should not appear in logs.

### LangGraph dependency or import failure

Resync the exact lockfile and rerun the import audit:

```bash
make setup
uv tree --project apps/agent | rg "langgraph|langchain-core|langchain-openai"
uv run --project apps/agent python -c "import langgraph, langchain_core, langchain_openai"
```

If setup cannot download packages, fix host proxy/DNS access. This is separate
from the intentionally network-disabled repository sandbox.

### The agent completes with no changes

The raw CLI returns `2`; the Makefile prints a warning and returns success.
Inspect `agent-final.json` and its `remaining_uncertainty`. Common causes are an
already-satisfied issue, ambiguous acceptance criteria, unavailable tooling, a
missing committed file, or a task the model could not solve responsibly.

Refine the issue or sandbox only after confirming the actual blocker. A retry
with unchanged inputs can produce a different model trajectory and consumes
another live request.

### A run directory exists but final artifacts are missing

The request, metadata, and issue are persisted before the model starts. If the
sandbox or runtime then fails, `agent-final.json`, `changed-files.json`, and
`diff.patch` may not exist. Use the run ID in the debug log and inspect the
partial directory:

```bash
find .sage/runs/<run-id> -maxdepth 2 -type f -print
make solve-debug REPO=... ISSUE=...
```

`make run-status` intentionally fails when a run is incomplete.

### A stale `sage-*` container remains

Normal success and failure paths remove the container. An abrupt host shutdown
or forcibly killed controller may prevent cleanup. Inspect exact targets first:

```bash
docker ps --all --filter name=sage-
```

If a listed container is confirmed to be stale, remove that exact container by
name:

```bash
docker rm --force sage-<exact-run-id>
```

Do not use a broad deletion command; other runs or unrelated containers may be
active.

## 13. Makefile command reference

| Command | Purpose | API call? |
| --- | --- | --- |
| `make help` | Prints targets and supported variables. | No |
| `make env` | Safely creates `.env` from the example. | No |
| `make setup` | Syncs the locked Python environment. | No model call; may download packages. |
| `make sandbox-build` | Builds the default Dockerfile under the selected tag. | No model call; may download image packages. |
| `make sandbox-smoke` | Verifies tools in a disposable isolated container. | No |
| `make doctor` | Checks host tools, daemon, image, key presence, and environment. | No |
| `make bootstrap` | Runs setup, image build, smoke test, and doctor in order. | No model call. |
| `make first-run REPO=... ISSUE=...` | Loads or prompts for configuration, performs complete setup and checks, then solves the issue. | Yes, at the final step. |
| `make test` | Runs deterministic pytest tests. | No |
| `make compile` | Compiles backend Python source. | No |
| `make check` | Runs `test` followed by `compile`. | No |
| `make graph` | Prints Mermaid from the compiled LangGraph using fakes. | No |
| `make new-issue ISSUE=...` | Copies the issue template without overwriting. | No |
| `make solve REPO=... ISSUE=...` | Runs the live V0 issue solver. | Yes |
| `make solve-debug REPO=... ISSUE=...` | Runs a live solve with debug logging. | Yes |
| `make run-status RUN_DIR=...` | Checks artifacts and summarizes the candidate diff. | No |
| `make run-test RUN_DIR=... TEST_COMMAND=...` | Tests a completed candidate in a fresh sandbox. | No |

## 14. Completion checklist

A manual V0 validation is complete when:

- [ ] `make doctor` reports all prerequisites as ready.
- [ ] `make check` succeeds without a live API call.
- [ ] `make graph` shows all five nodes and the `tools -> agent` loop.
- [ ] `make sandbox-smoke` succeeds and removes its container.
- [ ] The target base resolves to the intended committed SHA.
- [ ] The issue contains concrete acceptance criteria.
- [ ] A live solve creates a unique run directory.
- [ ] `changed-files.json` and `diff.patch` match the candidate Git state.
- [ ] Relevant tests pass inside an appropriate sandbox image.
- [ ] The original source checkout remains unchanged.
- [ ] The candidate patch has been reviewed before any manual application.
- [ ] No API key or secret appears in run artifacts or logs.
- [ ] No container from the completed run remains active.

At this point V0 has demonstrated its intended boundary: a written local issue
becomes an inspectable, tested candidate patch without mutating or publishing
from the source repository.
