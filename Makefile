SHELL := /bin/bash

.DEFAULT_GOAL := help

ROOT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
AGENT_PROJECT := apps/agent
DEFAULT_SANDBOX_IMAGE := sage-sandbox:v0

ENV_FILE ?= .env
ENV_PATH = $(if $(filter /%,$(ENV_FILE)),$(ENV_FILE),$(ROOT_DIR)/$(ENV_FILE))
SANDBOX_IMAGE ?=
REPO ?=
ISSUE ?=
BASE_REF ?= HEAD
RUN_DIR ?=
TEST_COMMAND ?= python3 -m unittest discover -v
REQUIRE_COMPLETED ?= false
V2_SAMPLE_DIR ?= $(ROOT_DIR)/v2-manual-test
DEBUG_FLAG :=

.PHONY: help env setup bootstrap first-run v2-first-run doctor github-doctor sandbox-build \
	sandbox-smoke test github-test github-event-check actions-check v1-check \
	v2-test v2-check v2-graph compile check graph new-issue solve solve-debug \
	run-status run-test

help: ## Show the available commands and variables.
	@printf '%s\n' \
		'Sage helper commands' \
		'' \
		'Getting started:' \
		'  make first-run REPO=... ISSUE=...' \
		'                        Configure, set up, verify, and solve in one command.' \
		'  make v2-first-run REPO=... ISSUE=...' \
		'                        Run strict live V2; omit both inputs to use the sample.' \
		'  make env              Create .env from .env.example (never overwrites).' \
		'  make bootstrap        Install Python deps, build/smoke-test the sandbox, run doctor.' \
		'  make doctor           Check tools, Docker, the sandbox image, and API-key setup.' \
		'' \
		'Development checks:' \
		'  make setup            Sync the locked Python environment with uv.' \
		'  make sandbox-build    Build the Docker sandbox image.' \
		'  make sandbox-smoke    Verify tools inside the network-disabled sandbox.' \
		'  make check            Run unit tests and compile the Python package.' \
		'  make github-test      Run the offline V1.0 GitHub gate checks.' \
		'  make github-event-check EVENT=...  Classify an event fixture offline.' \
		'  make actions-check     Validate V1.0 action/workflow syntax and policy.' \
		'  make v1-check          Run all deterministic V1.0 checks.' \
		'  make v2-test           Run focused offline V2 prototype tests.' \
		'  make v2-check          Run V2, Actions, and compile checks.' \
		'  make github-doctor     Diagnose the installed GitHub workflow.' \
		'  make graph            Print the compiled LangGraph Mermaid diagram.' \
		'  make v2-graph         Print/check the sequential V2 graph.' \
		'' \
		'Manual solve:' \
		'  make new-issue ISSUE=/absolute/path/to/issue.md' \
		'  make solve REPO=/absolute/path/to/repo ISSUE=/absolute/path/to/issue.md' \
		'  make solve-debug REPO=... ISSUE=...' \
		'  make run-status RUN_DIR=/absolute/path/to/run' \
		'  make run-test RUN_DIR=... TEST_COMMAND="python3 -m unittest -v"' \
		'' \
		'Optional variables:' \
		'  BASE_REF=HEAD                  Commit, branch, or tag to clone.' \
		'  SANDBOX_IMAGE=custom:v0        Override the configured sandbox image.' \
		'  ENV_FILE=.env                  Shell-format configuration file to load.' \
		'  LANGSMITH_TRACING=true         Enable named hosted traces (requires API key).' \
		'  LANGSMITH_PROJECT=sage-v2       Select the LangSmith project.' \
		'' \
		'See specs/06_V0.1_testing.md for the complete V0.1 walkthrough.' \
		'See specs/10_V1.0_testing.md for current GitHub migration checks.' \
		'See specs/15_SAGE_V2_PROTOTYPE_TESTING.md for V2 prototype checks.'

env: ## Create a local configuration file without overwriting an existing one.
	@set -euo pipefail; \
	cd "$(ROOT_DIR)"; \
	if [[ -e "$(ENV_PATH)" ]]; then \
		echo "Configuration already exists: $(ENV_PATH)"; \
		echo "No file was changed."; \
	else \
		cp .env.example "$(ENV_PATH)"; \
		echo "Created $(ENV_PATH)"; \
		echo "Next: add OPENAI_API_KEY, then run 'make bootstrap'."; \
	fi

setup: ## Install the locked backend environment.
	@set -euo pipefail; \
	command -v uv >/dev/null 2>&1 || { echo "ERROR: uv is not installed. See specs/03_V0_testing.md." >&2; exit 1; }; \
	cd "$(ROOT_DIR)"; \
	uv sync --project "$(AGENT_PROJECT)"

bootstrap: ## Perform the complete one-time setup in order.
	@$(MAKE) --no-print-directory setup
	@$(MAKE) --no-print-directory sandbox-build
	@$(MAKE) --no-print-directory sandbox-smoke
	@$(MAKE) --no-print-directory doctor

first-run: ## Configure, install, build, verify, and solve with one command.
	@set -euo pipefail; \
	cd "$(ROOT_DIR)"; \
	if [[ -z "$(REPO)" ]]; then \
		echo "ERROR: REPO is required. Use: make first-run REPO=/absolute/repo ISSUE=/absolute/issue.md" >&2; \
		exit 1; \
	fi; \
	if [[ -z "$(ISSUE)" ]]; then \
		echo "ERROR: ISSUE is required. Use: make first-run REPO=/absolute/repo ISSUE=/absolute/issue.md" >&2; \
		exit 1; \
	fi; \
	inherited_api_key="$${OPENAI_API_KEY:-}"; \
	if [[ -f "$(ENV_PATH)" ]]; then \
		echo "Loading configuration from $(ENV_PATH)"; \
		set -a; source "$(ENV_PATH)"; set +a; \
	fi; \
	if [[ -n "$$inherited_api_key" ]]; then export OPENAI_API_KEY="$$inherited_api_key"; fi; \
	if [[ -z "$${OPENAI_API_KEY:-}" ]]; then \
		if [[ ! -t 0 ]]; then \
			echo "ERROR: OPENAI_API_KEY is not configured and no interactive terminal is available." >&2; \
			echo "Set it in $(ENV_FILE) or export it before running make." >&2; \
			exit 1; \
		fi; \
		read -r -s -p "OpenAI API key (input hidden; used only for this run): " OPENAI_API_KEY; \
		echo; \
		if [[ -z "$$OPENAI_API_KEY" ]]; then \
			echo "ERROR: OPENAI_API_KEY cannot be empty." >&2; \
			exit 1; \
		fi; \
		export OPENAI_API_KEY; \
	fi; \
	: "$${OPENAI_MODEL:=gpt-5.4-mini}"; export OPENAI_MODEL; \
	: "$${OPENAI_MAX_RETRIES:=2}"; export OPENAI_MAX_RETRIES; \
	: "$${SAGE_MAX_TURNS:=30}"; export SAGE_MAX_TURNS; \
	: "$${SAGE_RUNS_DIR:=.sage/runs}"; export SAGE_RUNS_DIR; \
	: "$${SAGE_SANDBOX_IMAGE:=$(DEFAULT_SANDBOX_IMAGE)}"; export SAGE_SANDBOX_IMAGE; \
	: "$${SAGE_COMMAND_TIMEOUT_SECONDS:=60}"; export SAGE_COMMAND_TIMEOUT_SECONDS; \
	: "$${SAGE_MAX_TOOL_OUTPUT_CHARS:=12000}"; export SAGE_MAX_TOOL_OUTPUT_CHARS; \
	echo "Step 1/6: syncing the Python environment"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null setup; \
	echo "Step 2/6: building the Docker sandbox"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null sandbox-build; \
	echo "Step 3/6: smoke-testing the Docker sandbox"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null sandbox-smoke; \
	echo "Step 4/6: checking prerequisites"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null doctor; \
	echo "Step 5/6: running deterministic checks"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null check; \
	echo "Step 6/6: solving the issue"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null solve \
		REPO="$(REPO)" ISSUE="$(ISSUE)" BASE_REF="$(BASE_REF)" \
		SANDBOX_IMAGE="$(SANDBOX_IMAGE)"

v2-first-run: ## Configure, verify, and run a strict live V2 solve.
	@set -euo pipefail; \
	cd "$(ROOT_DIR)"; \
	requested_repo="$(REPO)"; \
	requested_issue="$(ISSUE)"; \
	if [[ -z "$$requested_repo" && -z "$$requested_issue" ]]; then \
		use_sample=1; \
		sample_project="$(V2_SAMPLE_DIR)/project"; \
		sample_issue="$(V2_SAMPLE_DIR)/issue.md"; \
		[[ -d "$$sample_project" ]] || { echo "ERROR: sample project is missing: $$sample_project" >&2; exit 1; }; \
		[[ -f "$$sample_issue" ]] || { echo "ERROR: sample issue is missing: $$sample_issue" >&2; exit 1; }; \
	elif [[ -z "$$requested_repo" || -z "$$requested_issue" ]]; then \
		echo "ERROR: REPO and ISSUE must be provided together." >&2; \
		echo "Use: make v2-first-run REPO=/absolute/repo ISSUE=/absolute/issue.md" >&2; \
		exit 1; \
	else \
		use_sample=0; \
		[[ -d "$$requested_repo" ]] || { echo "ERROR: repository path does not exist: $$requested_repo" >&2; exit 1; }; \
		[[ -f "$$requested_issue" ]] || { echo "ERROR: issue file does not exist: $$requested_issue" >&2; exit 1; }; \
	fi; \
	inherited_openai_api_key="$${OPENAI_API_KEY:-}"; \
	inherited_gemini_api_key="$${GEMINI_API_KEY:-}"; \
	inherited_context_approval="$${SAGE_GOOGLE_MODEL_CONTEXT_APPROVED:-}"; \
	inherited_langsmith_api_key="$${LANGSMITH_API_KEY:-}"; \
	inherited_langsmith_tracing="$${LANGSMITH_TRACING:-}"; \
	inherited_langsmith_project="$${LANGSMITH_PROJECT:-}"; \
	inherited_langsmith_workspace_id="$${LANGSMITH_WORKSPACE_ID:-}"; \
	if [[ -f "$(ENV_PATH)" ]]; then \
		echo "Loading configuration from $(ENV_PATH)"; \
		set -a; source "$(ENV_PATH)"; set +a; \
	fi; \
	if [[ -n "$$inherited_openai_api_key" ]]; then export OPENAI_API_KEY="$$inherited_openai_api_key"; fi; \
	if [[ -n "$$inherited_gemini_api_key" ]]; then export GEMINI_API_KEY="$$inherited_gemini_api_key"; fi; \
	if [[ -n "$$inherited_context_approval" ]]; then export SAGE_GOOGLE_MODEL_CONTEXT_APPROVED="$$inherited_context_approval"; fi; \
	if [[ -n "$$inherited_langsmith_api_key" ]]; then export LANGSMITH_API_KEY="$$inherited_langsmith_api_key"; fi; \
	if [[ -n "$$inherited_langsmith_tracing" ]]; then export LANGSMITH_TRACING="$$inherited_langsmith_tracing"; fi; \
	if [[ -n "$$inherited_langsmith_project" ]]; then export LANGSMITH_PROJECT="$$inherited_langsmith_project"; fi; \
	if [[ -n "$$inherited_langsmith_workspace_id" ]]; then export LANGSMITH_WORKSPACE_ID="$$inherited_langsmith_workspace_id"; fi; \
	for key_name in OPENAI_API_KEY GEMINI_API_KEY; do \
		if [[ -z "$${!key_name:-}" ]]; then \
			if [[ ! -t 0 ]]; then \
				echo "ERROR: $$key_name is not configured and no interactive terminal is available." >&2; \
				echo "Set it in $(ENV_FILE) or export it before running make." >&2; \
				exit 1; \
			fi; \
			read -r -s -p "$$key_name (input hidden; used only for this run): " key_value; \
			echo; \
			if [[ -z "$$key_value" ]]; then \
				echo "ERROR: $$key_name cannot be empty." >&2; \
				exit 1; \
			fi; \
			printf -v "$$key_name" '%s' "$$key_value"; \
			export "$$key_name"; \
		fi; \
	done; \
	context_approval="$${SAGE_GOOGLE_MODEL_CONTEXT_APPROVED:-false}"; \
	case "$${context_approval,,}" in \
		1|true|yes|on) export SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true ;; \
		*) \
			if [[ ! -t 0 ]]; then \
				echo "ERROR: SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true is required for V2." >&2; \
				exit 1; \
			fi; \
			read -r -p "Allow the selected Issue and repository context to be sent to the configured Google model? [y/N] " context_approval; \
			case "$${context_approval,,}" in \
				y|yes) export SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true ;; \
				*) echo "ERROR: Google model context use was not approved." >&2; exit 1 ;; \
			esac ;; \
	esac; \
	export SAGE_RUNTIME=v2-prototype; \
	export SAGE_MODEL_PROFILE=constrained-cross-provider; \
	: "$${LANGSMITH_TRACING:=false}"; export LANGSMITH_TRACING; \
	: "$${LANGSMITH_PROJECT:=sage-v2}"; export LANGSMITH_PROJECT; \
	if [[ "$$use_sample" -eq 1 ]]; then \
		export SAGE_VERIFICATION_COMMANDS_JSON='[{"id":"sample-unittest","command":"python3 calculator_checks.py","required":true,"timeout_seconds":60}]'; \
	fi; \
	: "$${SAGE_SANDBOX_IMAGE:=$(DEFAULT_SANDBOX_IMAGE)}"; export SAGE_SANDBOX_IMAGE; \
	echo "Step 1/8: syncing the Python environment"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null setup; \
	echo "Step 2/8: building the Docker sandbox"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null sandbox-build; \
	echo "Step 3/8: smoke-testing the Docker sandbox"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null sandbox-smoke; \
	echo "Step 4/8: checking V2 prerequisites"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null doctor; \
	echo "Step 5/8: running deterministic V2 checks"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null v2-check; \
	if [[ "$$use_sample" -eq 1 ]]; then \
		echo "Step 6/8: creating a disposable Git repository from $(V2_SAMPLE_DIR)"; \
		fixture_root="$$(mktemp -d "$${TMPDIR:-/tmp}/sage-v2-first-run.XXXXXX")"; \
		trap 'rm -rf -- "$$fixture_root"' EXIT; \
		target_repo="$$fixture_root/repo"; \
		target_issue="$$sample_issue"; \
		mkdir -p "$$target_repo"; \
		cp -R "$$sample_project/." "$$target_repo/"; \
		git init -q --initial-branch=main "$$target_repo"; \
		git -C "$$target_repo" config user.name "Sage V2 Local Test"; \
		git -C "$$target_repo" config user.email "sage-v2-local@example.invalid"; \
		git -C "$$target_repo" add --all; \
		git -C "$$target_repo" commit -q -m "test: initialize V2 manual fixture"; \
	else \
		echo "Step 6/8: using the requested repository and Issue"; \
		target_repo="$$requested_repo"; \
		target_issue="$$requested_issue"; \
	fi; \
	mkdir -p "$(ROOT_DIR)/.sage/runs"; \
	manual_run_root="$$(mktemp -d "$(ROOT_DIR)/.sage/runs/v2-manual.XXXXXX")"; \
	export SAGE_RUNS_DIR="$$manual_run_root"; \
	echo "Step 7/8: solving the Issue with the constrained V2 profile"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null REQUIRE_COMPLETED=true solve \
		REPO="$$target_repo" ISSUE="$$target_issue" BASE_REF="$(BASE_REF)"; \
	run_dir="$$(find "$$manual_run_root" -mindepth 1 -maxdepth 1 -type d -print -quit)"; \
	[[ -n "$$run_dir" ]] || { echo "ERROR: V2 solve did not create a run directory." >&2; exit 1; }; \
	echo "Step 8/8: validating the completed run artifacts and candidate diff"; \
	$(MAKE) --no-print-directory ENV_FILE=/dev/null run-status RUN_DIR="$$run_dir"; \
	echo; \
	echo "V2 local workflow succeeded."; \
	echo "Inspect the candidate and artifacts at: $$run_dir"

doctor: ## Check all prerequisites needed for a live solve.
	@set -euo pipefail; \
	cd "$(ROOT_DIR)"; \
	if [[ -f "$(ENV_PATH)" ]]; then set -a; source "$(ENV_PATH)"; set +a; fi; \
	status=0; \
	docker_ready=0; \
	for tool in git uv docker; do \
		if command -v "$$tool" >/dev/null 2>&1; then \
			echo "OK: $$tool -> $$(command -v "$$tool")"; \
		else \
			echo "ERROR: $$tool is not installed or is not on PATH." >&2; \
			status=1; \
		fi; \
	done; \
	if command -v docker >/dev/null 2>&1; then \
		if docker info >/dev/null 2>&1; then \
			echo "OK: Docker daemon is reachable."; \
			docker_ready=1; \
		else \
			echo "ERROR: Docker is installed, but its daemon is not reachable." >&2; \
			status=1; \
		fi; \
	fi; \
	image="$(SANDBOX_IMAGE)"; \
	if [[ -z "$$image" ]]; then image="$${SAGE_SANDBOX_IMAGE:-$(DEFAULT_SANDBOX_IMAGE)}"; fi; \
	if [[ "$$docker_ready" -eq 1 ]]; then \
		if docker image inspect "$$image" >/dev/null 2>&1; then \
			echo "OK: sandbox image exists ($$image)."; \
		else \
			echo "ERROR: sandbox image is missing ($$image). Run 'make sandbox-build'." >&2; \
			status=1; \
		fi; \
	else \
		echo "SKIP: sandbox image check requires a reachable Docker daemon."; \
	fi; \
	if [[ -n "$${OPENAI_API_KEY:-}" ]]; then \
		echo "OK: OPENAI_API_KEY is configured (value hidden)."; \
	else \
		echo "ERROR: OPENAI_API_KEY is empty. Add it to $(ENV_FILE)." >&2; \
		status=1; \
	fi; \
	langsmith_tracing="$${LANGSMITH_TRACING:-false}"; \
	case "$${langsmith_tracing,,}" in \
		1|true|yes|on) \
			if [[ -n "$${LANGSMITH_API_KEY:-}" ]]; then \
				echo "OK: LangSmith tracing is enabled (project configured; API key hidden)."; \
			else \
				echo "ERROR: LANGSMITH_API_KEY is required when LANGSMITH_TRACING=true." >&2; \
				status=1; \
			fi ;; \
		0|false|no|off|'') echo "OK: LangSmith tracing is disabled." ;; \
		*) echo "ERROR: LANGSMITH_TRACING must be true or false." >&2; status=1 ;; \
	esac; \
	if [[ "$${SAGE_RUNTIME:-v1}" == "v2-prototype" ]]; then \
		for key_name in GEMINI_API_KEY; do \
			if [[ -n "$${!key_name:-}" ]]; then \
				echo "OK: $$key_name is configured (value hidden)."; \
			else \
				echo "ERROR: $$key_name is required for V2." >&2; status=1; \
			fi; \
		done; \
		if [[ "$${SAGE_GOOGLE_MODEL_CONTEXT_APPROVED:-false}" == "true" ]]; then \
			echo "OK: Google model context use is explicitly acknowledged."; \
		else \
			echo "ERROR: SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true is required for V2." >&2; status=1; \
		fi; \
	fi; \
	if [[ -x "$(ROOT_DIR)/$(AGENT_PROJECT)/.venv/bin/python" ]]; then \
		python_version="$$($(ROOT_DIR)/$(AGENT_PROJECT)/.venv/bin/python --version 2>&1)"; \
		echo "OK: backend environment exists ($$python_version)."; \
	else \
		echo "ERROR: backend environment is missing. Run 'make setup'." >&2; \
		status=1; \
	fi; \
	exit "$$status"

sandbox-build: ## Build the default or configured sandbox image.
	@set -euo pipefail; \
	cd "$(ROOT_DIR)"; \
	command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker is not installed." >&2; exit 1; }; \
	docker info >/dev/null 2>&1 || { echo "ERROR: Docker daemon is not reachable." >&2; exit 1; }; \
	if [[ -f "$(ENV_PATH)" ]]; then set -a; source "$(ENV_PATH)"; set +a; fi; \
	image="$(SANDBOX_IMAGE)"; \
	if [[ -z "$$image" ]]; then image="$${SAGE_SANDBOX_IMAGE:-$(DEFAULT_SANDBOX_IMAGE)}"; fi; \
	echo "Building sandbox image: $$image"; \
	docker build --tag "$$image" --file docker/sandbox/Dockerfile .

sandbox-smoke: ## Start a disposable sandbox and verify its required tools.
	@set -euo pipefail; \
	cd "$(ROOT_DIR)"; \
	command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker is not installed." >&2; exit 1; }; \
	docker info >/dev/null 2>&1 || { echo "ERROR: Docker daemon is not reachable." >&2; exit 1; }; \
	if [[ -f "$(ENV_PATH)" ]]; then set -a; source "$(ENV_PATH)"; set +a; fi; \
	image="$(SANDBOX_IMAGE)"; \
	if [[ -z "$$image" ]]; then image="$${SAGE_SANDBOX_IMAGE:-$(DEFAULT_SANDBOX_IMAGE)}"; fi; \
	docker image inspect "$$image" >/dev/null 2>&1 || { echo "ERROR: sandbox image is missing ($$image). Run 'make sandbox-build'." >&2; exit 1; }; \
	docker run --rm \
		--network none \
		--cpus 1 \
		--memory 1g \
		--pids-limit 128 \
		--cap-drop ALL \
		--security-opt no-new-privileges \
		"$$image" \
		bash -lc 'git --version && python3 --version && rg --version'

test: ## Run deterministic unit tests (no API call required).
	@cd "$(ROOT_DIR)" && LANGSMITH_TRACING=false uv run --project "$(AGENT_PROJECT)" \
		pytest -c "$(AGENT_PROJECT)/pyproject.toml"

github-test: ## Run deterministic GitHub integration tests (no live API/model call).
	@cd "$(ROOT_DIR)" && LANGSMITH_TRACING=false uv run --project "$(AGENT_PROJECT)" \
		pytest -c "$(AGENT_PROJECT)/pyproject.toml" \
		"$(AGENT_PROJECT)/tests/integrations/github" \
		"$(AGENT_PROJECT)/tests/workflow/test_github_issue.py" \
		"$(AGENT_PROJECT)/tests/test_cli.py"

github-event-check: ## Parse and classify a local event fixture without API/model calls.
	@set -euo pipefail; \
	if [[ -z "$(EVENT)" ]]; then \
		echo "ERROR: EVENT is required. Example: make github-event-check EVENT=apps/agent/tests/fixtures/github/issue_solve.json" >&2; \
		exit 1; \
	fi; \
	cd "$(ROOT_DIR)"; \
	uv run --project "$(AGENT_PROJECT)" sage github event-check --event-file "$(EVENT)"

actions-check: ## Validate composite action/workflow syntax and security invariants.
	@cd "$(ROOT_DIR)" && uv run --project "$(AGENT_PROJECT)" \
		pytest -c "$(AGENT_PROJECT)/pyproject.toml" \
		"$(AGENT_PROJECT)/tests/actions"

v1-check: ## Run complete deterministic backend, GitHub, and Actions checks.
	@$(MAKE) --no-print-directory check
	@$(MAKE) --no-print-directory github-test
	@$(MAKE) --no-print-directory actions-check

v2-test: ## Run focused deterministic V2 tests without live provider calls.
	@cd "$(ROOT_DIR)" && LANGSMITH_TRACING=false uv run --project "$(AGENT_PROJECT)" \
		pytest -c "$(AGENT_PROJECT)/pyproject.toml" \
		"$(AGENT_PROJECT)/tests/providers" \
		"$(AGENT_PROJECT)/tests/repository" \
		"$(AGENT_PROJECT)/tests/context" \
		"$(AGENT_PROJECT)/tests/verification" \
		"$(AGENT_PROJECT)/tests/artifacts/test_v2_artifacts.py" \
		"$(AGENT_PROJECT)/tests/manual" \
		"$(AGENT_PROJECT)/tests/runtimes/v2" \
		"$(AGENT_PROJECT)/tests/test_config.py" \
		"$(AGENT_PROJECT)/tests/integrations/github/test_context.py" \
		"$(AGENT_PROJECT)/tests/integrations/github/test_status.py" \
		"$(AGENT_PROJECT)/tests/workflow/test_solve.py" \
		"$(AGENT_PROJECT)/tests/workflow/test_github_issue.py"

v2-check: ## Run all offline V2 prototype, Actions, and compile checks.
	@$(MAKE) --no-print-directory v2-test
	@$(MAKE) --no-print-directory actions-check
	@$(MAKE) --no-print-directory compile

github-doctor: ## Diagnose the installed GitHub workflow without printing secrets.
	@cd "$(ROOT_DIR)" && uv run --project "$(AGENT_PROJECT)" \
		python -m sage.integrations.github.doctor

compile: ## Compile all backend Python modules.
	@cd "$(ROOT_DIR)" && uv run --project "$(AGENT_PROJECT)" python -m compileall -q "$(AGENT_PROJECT)/src"

check: ## Run all deterministic backend checks.
	@$(MAKE) --no-print-directory test
	@$(MAKE) --no-print-directory compile

graph: ## Print Mermaid generated from the compiled V0.1 LangGraph.
	@cd "$(ROOT_DIR)" && LANGSMITH_TRACING=false uv run --project "$(AGENT_PROJECT)" \
		pytest -c "$(AGENT_PROJECT)/pyproject.toml" -q -s \
		"$(AGENT_PROJECT)/tests/runtimes/test_langgraph_graph.py::test_compiled_graph_renders_expected_mermaid"

v2-graph: ## Print and validate the compiled sequential V2 LangGraph.
	@cd "$(ROOT_DIR)" && LANGSMITH_TRACING=false uv run --project "$(AGENT_PROJECT)" \
		pytest -c "$(AGENT_PROJECT)/pyproject.toml" -q -s \
		"$(AGENT_PROJECT)/tests/runtimes/v2/test_graph.py::test_v2_graph_is_sequential_and_renders_mermaid"

new-issue: ## Copy the issue template to ISSUE; refuses to overwrite files.
	@set -euo pipefail; \
	cd "$(ROOT_DIR)"; \
	if [[ -z "$(ISSUE)" ]]; then \
		echo "ERROR: ISSUE is required. Example: make new-issue ISSUE=/tmp/my-issue.md" >&2; \
		exit 1; \
	fi; \
	issue_path="$(ISSUE)"; \
	if [[ -e "$$issue_path" ]]; then \
		echo "ERROR: refusing to overwrite existing file: $$issue_path" >&2; \
		exit 1; \
	fi; \
	mkdir -p "$$(dirname "$$issue_path")"; \
	cp examples/issue.md "$$issue_path"; \
	echo "Created issue template: $$issue_path"; \
	echo "Edit it before running 'make solve'."

solve: ## Run a live solve. CLI exit code 2 is shown as a warning, not a Make failure.
	@set -euo pipefail; \
	cd "$(ROOT_DIR)"; \
	if [[ -z "$(REPO)" ]]; then \
		echo "ERROR: REPO is required. Use an absolute path to a Git repository." >&2; \
		exit 1; \
	fi; \
	if [[ -z "$(ISSUE)" ]]; then \
		echo "ERROR: ISSUE is required. Use an absolute path to a Markdown or text file." >&2; \
		exit 1; \
	fi; \
	if [[ -f "$(ENV_PATH)" ]]; then set -a; source "$(ENV_PATH)"; set +a; fi; \
	if [[ -z "$${OPENAI_API_KEY:-}" ]]; then \
		echo "ERROR: OPENAI_API_KEY is empty. Run 'make env' and edit $(ENV_FILE)." >&2; \
		exit 1; \
	fi; \
	image_args=(); \
	if [[ -n "$(SANDBOX_IMAGE)" ]]; then image_args=(--sandbox-image "$(SANDBOX_IMAGE)"); fi; \
	set +e; \
	uv run --project "$(AGENT_PROJECT)" sage solve \
		--repo "$(REPO)" \
		--issue-file "$(ISSUE)" \
		--base-ref "$(BASE_REF)" \
		"$${image_args[@]}" \
		$(DEBUG_FLAG); \
	status=$$?; \
	set -e; \
	if [[ "$$status" -eq 2 ]]; then \
		if [[ "$(REQUIRE_COMPLETED)" == "true" ]]; then \
			echo "ERROR: this solve requires a completed, non-empty candidate (CLI exit code 2)." >&2; \
			exit 2; \
		fi; \
		echo; \
		echo "WARNING: the agent completed successfully but produced no repository change (CLI exit code 2)."; \
		exit 0; \
	fi; \
	exit "$$status"

solve-debug: DEBUG_FLAG := --debug
solve-debug: solve ## Run a live solve with debug logs and tracebacks.

run-status: ## Validate and summarize a completed run directory.
	@set -euo pipefail; \
	cd "$(ROOT_DIR)"; \
	if [[ -z "$(RUN_DIR)" ]]; then \
		echo "ERROR: RUN_DIR is required. Copy the run path printed by the solve." >&2; \
		exit 1; \
	fi; \
	run_dir="$(RUN_DIR)"; \
	for artifact in request.json metadata.json issue.md agent-final.json changed-files.json diff.patch; do \
		[[ -f "$$run_dir/$$artifact" ]] || { echo "ERROR: missing run artifact: $$run_dir/$$artifact" >&2; exit 1; }; \
	done; \
	[[ -d "$$run_dir/repo/.git" ]] || { echo "ERROR: candidate Git checkout is missing: $$run_dir/repo" >&2; exit 1; }; \
	echo "Run metadata:"; \
	sed -n '1,160p' "$$run_dir/metadata.json"; \
	echo; \
	echo "Authoritative changed files:"; \
	sed -n '1,200p' "$$run_dir/changed-files.json"; \
	echo; \
	echo "Candidate Git status:"; \
	git -C "$$run_dir/repo" status --short --untracked-files=all; \
	echo; \
	echo "Patch summary:"; \
	git -C "$$run_dir/repo" diff --stat; \
	git -C "$$run_dir/repo" diff --check; \
	echo "OK: required artifacts exist and 'git diff --check' passed."

run-test: ## Run TEST_COMMAND against a completed candidate inside a fresh sandbox.
	@set -euo pipefail; \
	cd "$(ROOT_DIR)"; \
	if [[ -z "$(RUN_DIR)" ]]; then \
		echo "ERROR: RUN_DIR is required. Copy the run path printed by the solve." >&2; \
		exit 1; \
	fi; \
	run_dir="$(RUN_DIR)"; \
	workspace="$$(cd "$$run_dir/repo" 2>/dev/null && pwd -P)" || { echo "ERROR: run workspace does not exist: $$run_dir/repo" >&2; exit 1; }; \
	command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker is not installed." >&2; exit 1; }; \
	docker info >/dev/null 2>&1 || { echo "ERROR: Docker daemon is not reachable." >&2; exit 1; }; \
	if [[ -f "$(ENV_PATH)" ]]; then set -a; source "$(ENV_PATH)"; set +a; fi; \
	image="$(SANDBOX_IMAGE)"; \
	if [[ -z "$$image" ]]; then image="$${SAGE_SANDBOX_IMAGE:-$(DEFAULT_SANDBOX_IMAGE)}"; fi; \
	docker image inspect "$$image" >/dev/null 2>&1 || { echo "ERROR: sandbox image is missing ($$image)." >&2; exit 1; }; \
	echo "Running in $$image: $(TEST_COMMAND)"; \
	docker run --rm \
		--network none \
		--cpus 2 \
		--memory 4g \
		--pids-limit 256 \
		--cap-drop ALL \
		--security-opt no-new-privileges \
		--env HOME=/tmp \
		--env "SAGE_TEST_COMMAND=$(TEST_COMMAND)" \
		--mount "type=bind,src=$$workspace,dst=/workspace" \
		--workdir /workspace \
		"$$image" \
		bash -lc 'eval "$$SAGE_TEST_COMMAND"'
