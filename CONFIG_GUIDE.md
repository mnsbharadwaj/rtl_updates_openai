# LLD Auto-Patcher — Configuration Guide

> Complete reference for `lld_patcher.yaml` and `workflow_config.yaml`

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Config File Reference — Batch Mode](#2-config-file-reference--batch-mode)
3. [Config File Reference — Workflow Mode](#3-config-file-reference--workflow-mode)
4. [LLM Backend Configuration](#4-llm-backend-configuration)
5. [GCC Gate & Compile Verification](#5-gcc-gate--compile-verification)
6. [Checkpoint & Resume](#6-checkpoint--resume)
7. [Common Scenarios](#7-common-scenarios)
8. [Complete Example Configs](#8-complete-example-configs)

---

## 1. Quick Start

### Generate a starter config:
```bash
# Batch mode (local directories):
python -m lld_gen.main_sfr init-config

# Full workflow (remote repos + PR):
python -m lld_gen.main_sfr init-workflow
```

### Run:
```bash
# Batch mode:
python -m lld_gen.main_sfr run-config --config lld_patcher.yaml

# Full workflow:
python -m lld_gen.main_sfr workflow --config workflow_config.yaml
```

---

## 2. Config File Reference — Batch Mode

File: **`lld_patcher.yaml`**

### 2.1 Required Directory Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sfr_old_dir` | path | `./old_sfr` | Directory containing **previous version** SFR headers |
| `sfr_new_dir` | path | `./new_sfr` | Directory containing **updated** SFR headers |
| `lld_dir` | path | `./lld` | Directory containing **existing** LLD headers to patch |
| `output_dir` | path | same as `lld_dir` | Where patched LLD files are written |
| `tests_dir` | path | same as `lld_dir` | Where generated test files are written |

> [!NOTE]
> Paths can be relative (resolved from config file location) or absolute.

### 2.2 Pipeline Control

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `no_llm` | bool | `false` | `true` = disable LLM entirely, use templates only |
| `force_no_llm` | bool | `false` | `true` = if LLM is unavailable, skip the interactive prompt and proceed silently with template mode. **Use this for CI/CD pipelines.** |
| `no_git` | bool | `false` | `true` = skip all git operations (commit, push) |
| `compile_check` | bool | `true` | `true` = gcc is **REQUIRED** — pipeline stops if not found. `false` = skip gcc verification entirely |
| `gcc` | string | auto-detect | Explicit path to gcc (e.g. `/usr/bin/arm-none-eabi-gcc`). If not set, searches PATH |

### 2.3 LLM Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ollama_model` | string | `""` | Ollama model name (e.g. `qwen2.5-coder:7b`) — legacy flat field |
| `hf_token` | string | `""` | HuggingFace API token — legacy flat field |
| `llm_test_gen` | bool | `false` | `true` = use LLM to generate richer unit tests (in addition to templates) |
| `llm` | dict | `{}` | **Recommended**: Full LLM config block. See [Section 4](#4-llm-backend-configuration) |

### 2.4 Patching Flags (v3.0+)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_llm_retries` | int | `5` | Max LLM fix attempts per failing function |
| `per_fn_compile` | bool | `true` | `true` = gcc-check each function individually after LLM patch |
| `skip_reg_added` | bool | `true` | `true` = flag REG_ADDED for manual review (don't auto-patch) |
| `skip_reg_deleted` | bool | `true` | `true` = flag REG_DELETED for manual review (don't auto-patch) |
| `emit_enum_defines` | bool | `false` | `true` = generate `#define` enums for FIELD_ENUM_CHANGED |

### 2.5 Cross-Reference & Git

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cross_lld_scan` | bool | `true` | `true` = scan `lld_dir` for callers of changed functions |
| `lld_search_depth` | int | `3` | Directory depth for cross-LLD reference scan |
| `atomic_commits` | bool | `true` | `true` = one git commit per SFR file changed |
| `commit_prefix` | string | `feat(lld)` | Git commit message prefix |
| `github_url` | string | `""` | Repository URL for PR link generation |

### 2.6 IP Filtering & Overrides

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ip_list` | list | `[]` | Only process these IPs (empty = all). e.g. `[PMU, UART, CLK]` |
| `ip_overrides` | dict | `{}` | Per-IP config overrides. See example below |

```yaml
ip_overrides:
  PMU:
    no_llm: true          # PMU uses templates only
    ollama_model: ""
  UART:
    ollama_model: "qwen2.5-coder:14b"   # Use larger model for UART
```

---

## 3. Config File Reference — Workflow Mode

File: **`workflow_config.yaml`**

Inherits ALL fields from batch mode (Section 2) plus:

### 3.1 Repository Specifications

Each repo block has the same structure:

| Sub-field | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | `""` | Git clone URL (HTTPS or SSH) |
| `branch` | string | `main` | Branch to checkout |
| `tag` | string | `""` | Specific tag to checkout |
| `commit` | string | `""` | Specific commit hash |
| `path` | string | `""` | Sub-path inside repo (sparse checkout) |
| `token` | string | `""` | Auth token. If empty, reads from env var (see below) |

#### Repo Blocks

| Block | Purpose | Token Env Var |
|-------|---------|---------------|
| `ipxact` | IP-XACT XML register files (source of truth) | — |
| `lld_repo` | Current LLD/SFR code to patch | `GITHUB_TOKEN` or `GH_TOKEN` or `BB_TOKEN` |
| `pr_target` | Where to raise the PR (can be same as `lld_repo`) | Same as `lld_repo` |

### 3.2 Workflow-Specific Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `convert_script` | string | `./convert.py` | Path to IP-XACT → SFR converter script |
| `pr_title_prefix` | string | `feat(lld): SFR auto-patch` | PR title prefix |

---

## 4. LLM Backend Configuration

The `llm:` block supports **6 backends**. Set `backend:` to switch.

### 4.1 Ollama (Local — Recommended for Development)

```yaml
llm:
  backend: ollama
  model: "qwen2.5-coder:7b"        # Model name in Ollama
  # url: "http://localhost:11434"   # Default, only change if custom port
  temperature: 0.05                 # Low = more deterministic
  max_tokens: 600
  timeout: 120.0
  max_retries: 3
  context_limit: 4096
```

**Setup:**
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull model
ollama pull qwen2.5-coder:7b

# Start server (runs on port 11434)
ollama serve
```

### 4.2 OpenAI

```yaml
# llm:
#   backend: openai
#   model: "gpt-4o-mini"            # or "gpt-4o", "gpt-3.5-turbo"
#   # api_key: ""                   # reads from OPENAI_API_KEY env var
#   temperature: 0.1
#   max_tokens: 600
```

**Setup:** Set `OPENAI_API_KEY` environment variable.

### 4.3 Anthropic (Claude)

```yaml
# llm:
#   backend: anthropic
#   model: "claude-sonnet-4-20250514"
#   # api_key: ""                   # reads from ANTHROPIC_API_KEY env var
#   temperature: 0.1
#   max_tokens: 600
```

**Setup:** Set `ANTHROPIC_API_KEY` environment variable.

### 4.4 Google Gemini (via OpenAI-Compatible API)

```yaml
# llm:
#   backend: openai_compat
#   model: "gemini-2.5-flash"
#   url: "https://generativelanguage.googleapis.com/v1beta/openai"
#   # api_key: ""                   # reads from OPENAI_API_KEY env var
#   temperature: 0.1
#   max_tokens: 600
```

**Setup:** Set `OPENAI_API_KEY` env var to your Google AI Studio API key.

### 4.5 Azure OpenAI

```yaml
# llm:
#   backend: azure_openai
#   model: "your-deployment-name"
#   url: "https://your-resource.openai.azure.com"
#   api_version: "2024-02-01"
#   # api_key: ""                   # reads from AZURE_OPENAI_KEY env var
```

### 4.6 HuggingFace Inference API

```yaml
# llm:
#   backend: huggingface
#   model: "Qwen/Qwen2.5-Coder-7B-Instruct"
#   # api_key: ""                   # reads from HF_TOKEN env var
```

### 4.7 Any OpenAI-Compatible Server (Groq, LM Studio, vLLM)

```yaml
# llm:
#   backend: openai_compat
#   model: "llama-3.1-8b-instant"        # Groq example
#   url: "https://api.groq.com/openai/v1"
#   # api_key: ""
```

> [!TIP]
> **Only one backend is active at a time.** Uncomment the `llm:` block you want and comment out the rest. The pipeline reads the FIRST uncommented `llm:` block.

---

## 5. GCC Gate & Compile Verification

### 5.1 How It Works

```
compile_check: true  (default)
  ├── gcc found in PATH     → Pipeline runs with full compile verification
  └── gcc NOT found         → Pipeline STOPS with clear error message
                             → Checkpoint saved → re-run resumes from here

compile_check: false
  ├── gcc found in PATH     → Uses gcc for optional checks (best-effort)
  └── gcc NOT found         → Pipeline runs WITHOUT compile verification
                             → All patched functions accepted without check
```

> [!WARNING]
> Setting `compile_check: false` means LLM-generated C code is **not verified by the compiler**. This may result in broken LLD functions being committed. Only use for quick testing or when gcc is genuinely unavailable.

### 5.2 Installing GCC

| Platform | Command |
|----------|---------|
| Ubuntu/Debian | `sudo apt install gcc` |
| CentOS/RHEL | `sudo yum install gcc` |
| macOS | `brew install gcc` or `xcode-select --install` |
| Windows | `choco install mingw` or download from [mingw-w64.org](https://mingw-w64.org) |
| Cross-compile (ARM) | `sudo apt install gcc-arm-none-eabi` |

You can also set an explicit path:
```yaml
gcc: "C:/mingw64/bin/gcc.exe"           # Windows
gcc: "/usr/local/bin/arm-none-eabi-gcc" # Cross-compile
```

---

## 6. Checkpoint & Resume

### 6.1 How It Works

The pipeline saves a checkpoint file (`.lld_patcher_checkpoint.json`) in `output_dir` after:
- **GCC not found** (stage: `pre_gcc`)
- **LLM not found + user chose abort** (stage: `pre_llm`)
- **Each IP completed** (stage: `ip_done`)

On the next run, the pipeline detects the checkpoint and **skips already-completed IPs**.

### 6.2 Resume Flow

```
Run 1:  PMU ✓  UART ✓  CLK ✗ (gcc not found)
        → checkpoint saved: {completed: [PMU, UART], stage: pre_gcc}

Run 2:  (gcc now installed)
        → checkpoint loaded: skipping PMU, UART
        → CLK ✓  DMA ✓
        → checkpoint cleared ✓
```

### 6.3 Checkpoint File Location

```
output_dir/.lld_patcher_checkpoint.json
```

To **force a fresh run** (ignore checkpoint), delete this file:
```bash
rm output_dir/.lld_patcher_checkpoint.json
```

---

## 7. Common Scenarios

### 7.1 Local Development (No Git, No Cloud)

```yaml
sfr_old_dir: ./old_sfr
sfr_new_dir: ./new_sfr
lld_dir:     ./lld
output_dir:  ./output
tests_dir:   ./tests

no_llm: false
no_git: true
compile_check: true

llm:
  backend: ollama
  model: "qwen2.5-coder:7b"
```

### 7.2 CI/CD Pipeline (Non-Interactive)

```yaml
sfr_old_dir: ./old_sfr
sfr_new_dir: ./new_sfr
lld_dir:     ./lld
output_dir:  ./output
tests_dir:   ./tests

no_llm: false
no_git: false
compile_check: true
force_no_llm: true           # Skip interactive prompt in CI
atomic_commits: true

llm:
  backend: openai
  model: "gpt-4o-mini"
  # api_key from OPENAI_API_KEY env var
```

### 7.3 Template-Only Mode (No LLM at All)

```yaml
no_llm: true                  # Completely disable LLM
compile_check: true
```

> [!NOTE]
> In this mode, change types that require LLM (COMMENT_CHANGED, MULTI_CHANGED, etc.) are patched with deterministic templates. The template may not capture semantic nuances from description changes.

### 7.4 Skip GCC (Quick Testing)

```yaml
compile_check: false          # Don't require gcc
no_llm: false                 # Still use LLM
force_no_llm: true            # But don't prompt if LLM is down
```

### 7.5 Full Workflow with GitHub PR

```yaml
# Directories (created automatically from cloned repos)
sfr_old_dir: ./workspace/old_sfr
sfr_new_dir: ./workspace/new_sfr
lld_dir:     ./workspace/lld
output_dir:  ./workspace/output
tests_dir:   ./workspace/tests

# Pipeline
no_llm: false
no_git: false
compile_check: true
force_no_llm: false
atomic_commits: true

# LLM
llm:
  backend: ollama
  model: "qwen2.5-coder:7b"

# Repositories
lld_repo:
  url: "https://github.com/samsung/cxl-lld.git"
  branch: main
  # token: ""  # reads from GITHUB_TOKEN env

pr_target:
  url: "https://github.com/samsung/cxl-lld.git"
  branch: main

pr_title_prefix: "feat(lld): SFR auto-patch"
```

### 7.6 Cross-Team Shared Config

```yaml
# Team defaults — override per IP as needed
no_llm: false
compile_check: true
force_no_llm: true           # Non-interactive for shared server
cross_lld_scan: true
lld_search_depth: 5          # Deep scan for large repos

llm:
  backend: ollama
  model: "qwen2.5-coder:7b"

# Per-IP overrides
ip_list:
  - PMU
  - UART
  - CLK

ip_overrides:
  PMU:
    no_llm: true             # PMU is stable, templates sufficient
  CLK:
    max_llm_retries: 10      # CLK has complex changes
```

---

## 8. Complete Example Configs

### 8.1 Minimal Batch Config (`lld_patcher.yaml`)

```yaml
# ================================================================
# LLD Auto-Patcher — Batch Config (Minimal)
# ================================================================

# Input directories
sfr_old_dir: ./old_sfr
sfr_new_dir: ./new_sfr
lld_dir:     ./lld

# Output
output_dir:  ./output
tests_dir:   ./tests

# Compile verification (REQUIRED by default)
compile_check: true
# gcc: "/path/to/gcc"       # Uncomment to set explicit path

# LLM (Ollama local)
no_llm: false
force_no_llm: false          # true = skip prompt, false = ask user
llm:
  backend: ollama
  model: "qwen2.5-coder:7b"
  temperature: 0.05

# Git
no_git: true
```

### 8.2 Full Workflow Config (`workflow_config.yaml`)

```yaml
# ================================================================
# LLD Auto-Patcher — Full Workflow Config
# ================================================================

# ── Directories ──────────────────────────────────────────────────
sfr_old_dir: ./workspace/old_sfr
sfr_new_dir: ./workspace/new_sfr
lld_dir:     ./workspace/lld
output_dir:  ./workspace/output
tests_dir:   ./workspace/tests

# ── Pipeline Control ─────────────────────────────────────────────
compile_check: true           # Require gcc (stop if missing)
no_llm:       false           # Use LLM for complex changes
force_no_llm: false           # Prompt user if LLM unavailable
no_git:       false           # Enable git operations
atomic_commits: true          # One commit per IP
commit_prefix: "feat(lld)"

# ── GCC ──────────────────────────────────────────────────────────
# gcc: "/usr/bin/gcc"         # Auto-detects from PATH if not set

# ── LLM Backend ─────────────────────────────────────────────────
# Active: Ollama (local Qwen)
llm:
  backend: ollama
  model: "qwen2.5-coder:7b"
  temperature: 0.05
  max_tokens: 600
  timeout: 120.0
  max_retries: 3
  context_limit: 4096

# ── Alternative LLM backends (uncomment ONE to switch) ──────────
# llm:
#   backend: openai
#   model: "gpt-4o-mini"
#   temperature: 0.1
#   max_tokens: 600
#   # api_key reads from OPENAI_API_KEY env var

# llm:
#   backend: anthropic
#   model: "claude-sonnet-4-20250514"
#   temperature: 0.1
#   max_tokens: 600
#   # api_key reads from ANTHROPIC_API_KEY env var

# llm:
#   backend: openai_compat
#   model: "gemini-2.5-flash"
#   url: "https://generativelanguage.googleapis.com/v1beta/openai"
#   temperature: 0.1
#   max_tokens: 600

# ── Patching Flags ───────────────────────────────────────────────
max_llm_retries: 5            # LLM fix retries per function
per_fn_compile: true          # Compile each function after patch
skip_reg_added: true          # Flag REG_ADDED for manual review
skip_reg_deleted: true        # Flag REG_DELETED for manual review
cross_lld_scan: true          # Scan for callers of changed functions
lld_search_depth: 3           # Directory depth for cross-ref scan
emit_enum_defines: false      # Generate #define for enum changes

# ── Repositories ─────────────────────────────────────────────────
lld_repo:
  url: "https://github.com/your-org/your-repo.git"
  branch: main
  # token: ""                 # Reads from GITHUB_TOKEN env var

pr_target:
  url: "https://github.com/your-org/your-repo.git"
  branch: main

pr_title_prefix: "feat(lld): SFR auto-patch"

# ── IP-XACT (optional) ──────────────────────────────────────────
# ipxact:
#   url: "https://github.com/your-org/ip-xact-specs.git"
#   branch: main
#   path: "register_maps/"
# convert_script: "./convert.py"

# ── IP Filtering (optional) ─────────────────────────────────────
# ip_list:
#   - PMU
#   - UART
#   - CLK

# ── Per-IP Overrides (optional) ─────────────────────────────────
# ip_overrides:
#   PMU:
#     no_llm: true
#   UART:
#     max_llm_retries: 10
```

---

> [!TIP]
> **Config validation**: Run `python -m lld_gen.main_sfr run-config --config your_config.yaml` with `no_git: true` to do a dry run that validates your config without making any git changes.

*LLD Auto-Patcher v3.1 — Configuration Guide*
