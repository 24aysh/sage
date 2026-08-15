# AGENTS.md

## Purpose

This repository contains a production-oriented Python multi-agent system.

These instructions apply to AI coding agents working in this repository. Optimize for:

- correctness before speed;
- reuse before duplication;
- simple and modular production code;
- clear ownership between agents, orchestration, tools, state, and integrations;
- minimal dependency and runtime footprint;
- focused, reviewable changes;
- clean Git history with logical commits.

Do not treat a task request as permission to redesign unrelated parts of the repository.

---

## 1. Understand Before You Change

Do not start coding immediately.

Before implementing a feature, fix, refactor, or utility:

1. Read the relevant repository structure and configuration.
2. Search for an existing implementation, abstraction, helper, tool, agent, schema, protocol, or test that already solves all or part of the task.
3. Inspect call sites before changing shared behavior.
4. Inspect existing tests to understand expected behavior.
5. Check the project's established patterns before introducing a new one.

Use repository search tools such as `rg`, `find`, symbol search, or IDE references where available.

At minimum, search for:

- the requested feature/function name;
- related domain terminology;
- similar agent or tool implementations;
- existing interfaces/base classes/protocols;
- configuration keys;
- relevant tests.

### Reuse rule

Prefer, in order:

1. use an existing implementation unchanged;
2. extend an existing implementation when its responsibility still fits;
3. extract shared logic into a reusable function/module;
4. create a new abstraction only when the existing design cannot reasonably support the requirement.

Do not create duplicate helpers with slightly different names or behavior.

If functionality will reasonably be reused in two or more places, place it behind a focused function, class, protocol, or module with a clear responsibility.

Do not over-abstract speculative future use cases.

---

## 2. Plan the Change Before Coding

For non-trivial changes, establish a short implementation plan before editing.

The plan should identify:

- existing code that can be reused;
- files/modules likely to change;
- behavior being added or changed;
- tests that need to be added or updated;
- compatibility or migration concerns;
- whether a new dependency is actually necessary.

Prefer the smallest implementation that fully solves the task.

Avoid unrelated cleanup unless it is required to make the requested change safe.

---

## 3. Multi-Agent Architecture Rules

Keep responsibilities explicit.

A healthy separation generally looks like:

- **agents** — agent definitions, roles, prompts/instructions, capabilities;
- **orchestration** — routing, handoffs, workflow/graph execution, coordination;
- **tools** — deterministic capabilities agents may invoke;
- **models / schemas** — typed request, response, event, state, and domain models;
- **memory / state** — session state, persistence, checkpoints, long-term memory;
- **providers / integrations** — model vendors and external service adapters;
- **config** — settings and configuration loading;
- **observability** — logging, tracing, metrics, evaluation hooks;
- **utils** — small genuinely shared utilities only;
- **tests** — tests mirroring production behavior and module boundaries.

Do not force this exact layout onto an established repository if an equivalent structure already exists. Follow the existing architecture unless there is a clear reason to improve it.

### Agent design

Keep agent definitions thin.

Do not hide substantial deterministic business logic inside prompts or agent construction code when it belongs in normal Python functions or tools.

Agent-specific code should primarily describe:

- role/responsibility;
- instructions;
- allowed tools/capabilities;
- handoff/routing rules;
- model/runtime configuration when applicable.

### Orchestration

Keep orchestration separate from individual agent implementations when practical.

Routing and workflow decisions should be testable without requiring a live model call whenever possible.

Avoid circular dependencies between agents, tools, orchestration, and state modules.

### Tools

Tool functions should:

- do one clear thing;
- have explicit inputs and outputs;
- be independently testable;
- avoid hidden global state;
- surface useful errors;
- reuse existing domain/service functions rather than duplicate business logic.

Do not make model calls from a deterministic tool unless the tool is explicitly designed as an AI/model-backed capability.

### Provider boundaries

Keep provider-specific behavior behind adapters or integration modules when the project supports multiple model/service providers.

Do not leak provider-specific response shapes throughout the core domain unless the repository intentionally depends on that provider.

---

## 4. Python Code Quality

Write simple, production-style Python.

Prefer:

- small focused functions;
- clear names over cleverness;
- explicit control flow;
- type hints for public and non-trivial interfaces;
- standard library features when they are sufficient;
- existing project abstractions and dependencies;
- composition over unnecessary inheritance;
- immutable or narrowly scoped state where practical.

Avoid:

- premature abstraction;
- giant manager/service classes;
- catch-all `utils.py` modules;
- hidden side effects;
- mutable module-level state;
- deeply nested conditionals when a clearer decomposition exists;
- duplicated constants or magic strings;
- broad `except Exception` blocks unless re-raised or handled intentionally;
- unnecessary wrappers around third-party APIs;
- abstractions that have only one trivial caller and add no clarity.

### Reusable logic

Extract a function or module when logic:

- is used in multiple places;
- represents a clear domain operation;
- is complex enough to deserve isolated tests;
- would otherwise make orchestration/agent code difficult to read.

Do not extract one-line helpers merely to increase the number of functions.

---

## 5. Keep the Dependency Footprint Small

Treat new production dependencies as a cost.

Before adding a package:

1. confirm the repository does not already have equivalent functionality;
2. check whether the Python standard library is sufficient;
3. check whether an already-installed dependency can handle the requirement cleanly;
4. add a new dependency only when it materially improves correctness, maintainability, or required capability.

Do not add large frameworks for small features.

Do not introduce a second library that overlaps substantially with an existing dependency.

If a dependency is added or upgraded, update only the appropriate dependency/lock files and avoid unrelated version churn.

---

## 6. Project Structure

Keep the repository easy to navigate.

Prefer domain/responsibility-based modules over dumping unrelated code into a single directory.

For a new multi-agent project without an established layout, a reasonable starting point is:

```text
src/
  <package_name>/
    agents/
    orchestration/
    tools/
    models/
    memory/
    providers/
    config/
    observability/
    utils/

tests/
  agents/
  orchestration/
  tools/
  memory/
  ...
```

This is guidance, not a requirement to create every directory.

Create a directory only when it has a real responsibility and enough content to justify it.

Keep tests close in conceptual structure to the code they verify.

Avoid ambiguous directories such as:

- `misc/`
- `common/`
- `helpers/`

unless the repository already uses them with a well-defined meaning.

---

## 7. Configuration and Secrets

Never hardcode secrets, API keys, tokens, credentials, or environment-specific private values.

Follow the repository's existing configuration system.

Prefer environment variables or the project's settings abstraction for secrets.

When adding configuration:

- use typed/defaulted configuration where the project already supports it;
- validate required values at an appropriate boundary;
- do not read environment variables from many unrelated modules;
- document newly required configuration in the repository's existing example/env documentation.

Do not commit real `.env` files or credentials.

---

## 8. Error Handling and Reliability

Fail clearly at system boundaries.

Use domain-specific exceptions or structured error results when they improve handling.

Preserve useful context when wrapping exceptions.

For multi-agent workflows:

- define what happens when an agent/tool/handoff fails;
- avoid silent fallback behavior unless explicitly intended;
- make retry behavior bounded and configurable;
- avoid infinite agent loops;
- put explicit limits on recursive/iterative orchestration where applicable;
- make cancellation/timeouts possible for network or model operations when supported by the existing stack.

Do not retry deterministic validation errors.

---

## 9. Logging and Observability

Use the project's logging/tracing facilities instead of `print()` in production code.

Log useful operational context without leaking secrets or full sensitive payloads.

For multi-agent execution, prefer structured visibility into:

- agent/workflow identity;
- handoffs;
- tool calls;
- failures;
- latency;
- retry attempts;
- important state transitions.

Do not add noisy logs for every small internal operation.

---

## 10. Tests and Verification

Before changing code, inspect how this repository runs:

- unit tests;
- integration tests;
- formatting;
- linting;
- type checking.

Discover commands from existing files such as:

- `pyproject.toml`;
- `Makefile`;
- `tox.ini`;
- `noxfile.py`;
- CI workflow files;
- project documentation.

Do not invent a second toolchain when one already exists.

### Testing expectations

For changed behavior:

- add or update focused tests;
- test reusable functions directly;
- test orchestration/routing decisions where practical;
- mock external model/network boundaries rather than over-mocking internal logic;
- include failure-path tests for meaningful edge cases.

Prefer deterministic tests.

Do not require live paid API calls for the normal unit test suite unless that is already an explicit project convention.

### Verification order

Prefer:

1. focused tests for the changed module;
2. relevant integration tests;
3. formatter/linter/type checker used by the project;
4. broader test suite when appropriate.

Do not claim tests passed unless they were actually run successfully.

If a check cannot be run, state that clearly.

---

## 11. Git Safety

Preserve user work.

Before editing or committing, inspect:

```bash
git status --short
git diff
git diff --staged
```

Do not overwrite, revert, stage, or commit unrelated user changes.

Do not use destructive Git commands such as `git reset --hard`, `git clean -fd`, or forced checkout of user files unless the user explicitly requests and understands the destructive action.

---

## 12. Commit Strategy

You are not allowed to commit by yourself, when the user asks to commit, then only you will commit based on the below defined behaviour

When the task includes committing changes or you are otherwise authorized to create commits, do **not** put every change into one large commit by default.

First inspect the complete diff and divide it into the smallest reasonable number of **logical, independently understandable commits**.

The goal is not to maximize the number of commits. The goal is for each commit to represent one coherent purpose.

### Before committing

Review:

```bash
git status --short
git diff --stat
git diff
git diff --staged
```

Then determine commit boundaries.

### Good commit boundaries

Separate commits when changes represent different concerns, for example:

- refactoring existing code before adding new behavior;
- adding a reusable core abstraction;
- implementing a new feature on top of that abstraction;
- unrelated configuration/build changes;
- documentation-only changes unrelated to implementation;
- dependency upgrades unrelated to feature code.

Tests that directly validate a feature or fix should usually stay with the implementation commit so that the commit is internally verifiable.

Do not split tightly coupled implementation and tests merely to create more commits.

Do not mix unrelated formatting or cleanup into a feature commit.

### Commit messages

Use short, production-style Conventional Commit messages:

```text
<type>(optional-scope): <short purpose>
```

Preferred types include:

- `feat` — new behavior/capability;
- `fix` — bug fix;
- `refactor` — restructuring without intended behavior change;
- `perf` — performance improvement;
- `test` — test-only change;
- `docs` — documentation-only change;
- `chore` — maintenance work that fits no better category;
- `build` — dependency/build/package changes;
- `ci` — CI workflow changes.

Examples for this repository:

```text
feat(orchestrator): add agent handoff routing
feat(tools): add reusable tool registry
fix(memory): preserve session state on retry
refactor(agents): share agent factory logic
test(orchestrator): cover invalid handoff target
chore(config): add default workflow limits
build(deps): bump openai dependency
docs: document agent workflow setup
```

For dependency/version upgrades, prefer messages such as:

```text
build(deps): bump <package>
```

or, if that better matches the repository convention:

```text
chore(deps): bump <package>
```

Do not introduce a custom `bump:` type unless the repository already uses it.

### Atomic commit rule

Each commit should ideally:

- have one clear purpose;
- leave the repository in a coherent state;
- include relevant tests for its behavior;
- avoid unrelated file changes;
- be understandable from its diff and message.

If the complete task is genuinely one atomic change, one commit is acceptable. Never split changes artificially just to satisfy a commit count.

After each commit, inspect the remaining changes with:

```bash
git status --short
git diff
```

Before finishing, inspect the resulting history:

```bash
git log --oneline -n 10
```

---

## 13. Change Scope

Make the smallest safe change that satisfies the requirement.

Do not:

- refactor unrelated modules;
- rename public APIs without a reason;
- move large directory trees for cosmetic reasons;
- update dependency versions incidentally;
- change formatting across untouched files;
- introduce a framework because it may be useful later.

When broader cleanup is valuable but not required, mention it separately rather than bundling it into the task.

---

## 14. Documentation

Update documentation when a change affects:

- public APIs;
- environment/configuration requirements;
- project setup;
- architecture developers need to understand;
- agent/tool extension points;
- behavior users depend on.

Keep code self-explanatory. Comments should explain **why**, constraints, or non-obvious decisions rather than narrating obvious code.

---

## 15. Completion Checklist

Before marking a coding task complete, confirm:

- [ ] I searched for an existing implementation before creating a new one.
- [ ] I reused or extended existing code where appropriate.
- [ ] Reusable logic is placed behind an appropriate function/module.
- [ ] The code follows the repository's existing structure and conventions.
- [ ] I did not add an unnecessary dependency.
- [ ] Agent, orchestration, tool, provider, and state responsibilities remain clear.
- [ ] Relevant tests were added or updated.
- [ ] Relevant tests/checks were actually run, or I clearly stated what could not be run.
- [ ] I inspected the final diff for accidental/unrelated changes.
- [ ] I preserved pre-existing user changes.
- [ ] If commits were requested, I split the work by logical concern rather than committing everything blindly.
- [ ] Commit messages are short and accurately describe their purpose.

---

## 16. Final Response Expectations

When reporting completed work, briefly state:

1. what was changed;
2. what existing code was reused or extended;
3. tests/checks that were run and their result;
4. any important limitation or follow-up;
5. if commits were created, list the commits and explain the logical split.

Do not claim success for actions that were not performed.
