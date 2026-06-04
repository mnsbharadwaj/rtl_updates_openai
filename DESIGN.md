# LLD Auto-Patcher — Design Document & Code Flow Reference

> **Version:** 2.0 — Struct-Based LLD + Ollama LLM Support  
> **Branch:** `feature/struct-based-lld`  
> **Tests:** 206/206 passing ✅

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Module Reference](#2-module-reference)
3. [Complete Code Flow](#3-complete-code-flow)
4. [Struct-Based LLD Architecture](#4-struct-based-lld-architecture)
5. [How to Use — Template Mode (No LLM)](#5-how-to-use--template-mode-no-llm)
6. [How to Use — Ollama LLM Mode (Local)](#6-how-to-use--ollama-llm-mode-local)
7. [How to Use — HuggingFace LLM Mode (Cloud)](#7-how-to-use--huggingface-llm-mode-cloud)
8. [Config File Reference](#8-config-file-reference)
9. [Traceability Matrix — 12 Change Types](#9-traceability-matrix--12-change-types)
10. [Unit Test Catalog](#10-unit-test-catalog)
11. [LLM Prompt Reference](#11-llm-prompt-reference)
12. [SRC_SHA Anchor Mechanism](#12-srcssha-anchor-mechanism)
13. [LLM vs Template Comparison Tool](#13-llm-vs-template-comparison-tool)
14. [Quick Reference Card](#14-quick-reference-card)

---

## 1. System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       LLD Auto-Patcher System v2.0                          │
│                                                                              │
│   Input Files                                                                │
│   ──────────                                                                 │
│   old_sfr/sfr_pmu.h ──┐                                                     │
│   new_sfr/sfr_pmu.h ──┼──► sfr_diff_analyzer.py ──► ChangeRecord[]         │
│                        │    (Samsung union bitfield                │          │
│                        │     OR #define MASK/SHIFT parser)         │          │
│   lld/lld_pmu.h ───────┼────────────────────────────────────────► │          │
│                        │                 lld_patcher.py            │          │
│                        │     (Struct-Based Surgical Patcher        │          │
│                        │      + Struct Test Generator)             │          │
│                        │              │           │                │          │
│   LLM Backends ────────┤              │           │                          │
│   ┌────────────────┐   │              ▼           ▼                          │
│   │ Ollama (local) │◄──┤        llm_client.py                                │
│   │ qwen2.5-coder  │   │   (Ollama-first + HuggingFace fallback)             │
│   └────────────────┘   │   • COMMENT_CHANGED → LLM rewrites docstring       │
│   ┌────────────────┐   │   • MULTI_CHANGED   → LLM handles combined          │
│   │ HuggingFace    │◄──┘   • FIELD_ADDED     → template + optional LLM      │
│   │ Qwen2.5-7B     │       • llm_test_gen     → struct-based unit tests      │
│   └────────────────┘                                                          │
│                                                                              │
│   Output Files                                                               │
│   ────────────                                                               │
│   lld_patched/lld_pmu.h              ← patched, struct-based LLD            │
│   tests_generated/test_lld_pmu.c    ← struct-init unit tests                │
│   lld_patched/PR_DESCRIPTION.md     ← change table + deprecated fn list     │
│                                          + GitHub PR URL                     │
│                                                                              │
│   Pipeline Steps                                                             │
│   ──────────────                                                             │
│   [1/4] Diff & Classify  →  sfr_diff_analyzer.py                           │
│   [2/4] Patch LLD        →  lld_patcher.py + llm_client.py                 │
│   [3/4] Compile Check    →  compile_check.py  (gcc -fsyntax-only)           │
│   [4/4] Stage PR         →  pr_stage.py  (PR_DESCRIPTION.md + git add)      │
│                                                                              │
│   Orchestration                                                              │
│   ─────────────                                                              │
│   main_sfr.py     CLI: run / diff / patch / run-config / init-config        │
│   batch_runner.py multi-IP pipeline loop                                     │
│   config.py       YAML reader + file matcher + ollama_model field            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Module Reference

| File | Purpose | Key Classes / Functions |
|------|---------|------------------------|
| [sfr_diff_analyzer.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/sfr_diff_analyzer.py) | Parse SFR headers → IR; diff old vs new → 12 change types | `SfrParser`, `SfrDiffAnalyzer`, `classify_sfr_diff()` |
| [lld_patcher.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/lld_patcher.py) | Struct-based surgical LLD patcher + test generator | `LLDPatcher`, `generate_field_functions()`, `generate_test_for_field()` |
| [llm_client.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/llm_client.py) | Ollama-first + HuggingFace fallback LLM client | `LLMClient.generate()`, `LLMClient.generate_test()`, `fix_compile_error()` |
| [compile_check.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/compile_check.py) | Run `gcc -fsyntax-only` on patched LLD | `run_compile_check()`, `CompileResult` |
| [pr_stage.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/pr_stage.py) | Generate PR_DESCRIPTION.md + git add + deprecated fn list | `stage_pr()`, `build_pr_description()` |
| [batch_runner.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/batch_runner.py) | Multi-IP pipeline loop | `BatchRunner`, `BatchRunner.run()` |
| [config.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/config.py) | Read YAML config; discover IP jobs | `PatcherConfig`, `load_config()`, `discover_jobs()` |
| [main_sfr.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/main_sfr.py) | CLI entry point | `_cmd_run()`, `_cmd_diff()`, `_cmd_run_config()`, `_cmd_init_config()` |
| [compare_llm_vs_template.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/compare_llm_vs_template.py) | LLM vs template side-by-side comparison | `run_comparison()` |

---

## 3. Complete Code Flow

### 3.1 High-level pipeline (`run-config`)

```
python -m lld_gen.main_sfr run-config --config lld_patcher.yaml
    │
    ├─ 1. load_config(yaml)
    │      → PatcherConfig (directories, LLM settings, ollama_model, flags)
    │
    ├─ 2. discover_jobs(cfg)
    │      → List[IPJob]  (matched old/new SFR + LLD file per IP)
    │
    └─ 3. BatchRunner.run()
           │
           └─ for each IPJob:
              │
              ├─ STEP 1: classify_sfr_diff(old, new)
              │     SfrParser.parse_file(old) → SfrIR
              │     SfrParser.parse_file(new) → SfrIR
              │     SfrDiffAnalyzer.analyze() → List[ChangeRecord]
              │
              ├─ STEP 2: LLDPatcher.patch()
              │     ├─ _regen_struct_header()  ← rebuild SFR_IP aggregate struct
              │     ├─ _migrate_block_to_struct() ← auto-upgrade old base[] style
              │     ├─ split lld.h into SRC_SHA blocks
              │     ├─ For each ChangeRecord → _apply(block, cr)
              │     │     Template path OR LLM path (see §3.3)
              │     ├─ Reassemble + brace check (_check_braces)
              │     └─ write_test_file() → struct-init test_lld_pmu.c
              │
              ├─ STEP 3: run_compile_check()
              │     gcc -fsyntax-only test_lld_pmu.c
              │     If fail → LLM.fix_compile_error() (up to 3 retries)
              │
              └─ STEP 4: stage_pr()
                    PR_DESCRIPTION.md:
                      - Change table (all 12 types)
                      - ⚠ Deprecated LLD Functions (from REG_DELETED)
                      - GitHub PR URL
                    git add (if no_git: false)
```

### 3.2 LLM Backend Auto-detection Flow

```
LLMClient.__init__(ollama_model="qwen2.5-coder", hf_token="")
    │
    ├─ _detect_backend()
    │     │
    │     ├─ if ollama_model set:
    │     │     GET http://localhost:11434/api/tags
    │     │     → models list
    │     │     → partial match: "qwen2.5-coder" matches "qwen2.5-coder:1.5b"
    │     │     → backend = "ollama"  ✅
    │     │
    │     └─ elif hf_token set:
    │           → backend = "huggingface"
    │
    ├─ generate(...)
    │     → cache key = SHA16(reg|field|change_type|desc)
    │     → check .lld_gen_cache/{key}.c
    │     → if miss: _call(system_prompt, user_prompt)
    │          "ollama"      → POST /api/chat  (stream=false)
    │          "huggingface" → POST HF_API_URL (chat completions)
    │     → strip markdown fences
    │     → save to cache
    │     → return raw C code
    │
    └─ generate_test(...)
          same cache + dispatch logic
          → struct-based test code (SFR_IP sfr; struct lld_ip lld)
```

### 3.3 Data Structures at Each Stage

```
Stage 1 — Parse
────────────────
SfrParser.parse_file()  →  SfrIR
                               │
                               └─ Dict[reg_name → RegisterIR]
                                            │
                                            └─ name, offset, reset_val,
                                               sha16, desc,
                                               Dict[field_name → FieldIR]
                                                            │
                                                            └─ name, mask, shift,
                                                               msb, lsb, access,
                                                               desc, reset_val,
                                                               reg_name, ip,
                                                               return_type

Stage 2 — Diff
───────────────
SfrDiffAnalyzer.analyze(old_ir, new_ir)  →  List[ChangeRecord]

  ChangeRecord:
    reg_name    : str           # "STATUS_CON"
    field_name  : str | None    # "THRESH"
    change_type : str           # "BITWIDTH_CHANGED"
    old_field   : FieldIR       # field as it was
    new_field   : FieldIR       # field as it should be
    old_reg     : RegisterIR
    new_reg     : RegisterIR
    needs_llm   : bool          # True for COMMENT/MULTI/ADDED-with-desc
    details     : List[str]     # human-readable reason list

Stage 3 — Patch
────────────────
lld.h split into:
  List[ (reg_name: str, sha: str, block: str) ]
       │
       Each block = one register's struct-based static inline functions
       Blocks separated by SRC_SHA anchor comments

  For each ChangeRecord, _apply() routes:
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ REG_RENAMED     → _patch_struct_member_ref(block, old, new)             │
  │                   FREEZE fn names; only update lld->pSFR->stNEW. refs   │
  │                                                                          │
  │ REG_DELETED     → _deprecate_block(block)                               │
  │                   KEEP functions + add DEPRECATED comment               │
  │                   collect fn names → deprecated_fns list (for PR notes) │
  │                                                                          │
  │ REG_ADDED       → _generate_struct_block() → append before #endif       │
  │                   full new struct-based block + SRC_SHA                 │
  │                                                                          │
  │ FIELD_RENAMED   → _patch_field_name_in_body(block, old, new)            │
  │                   FREEZE fn names; only stNative.NEWFIELD body ref       │
  │                                                                          │
  │ FIELD_DELETED   → _remove_field_functions_raw(block, ip, reg, field)    │
  │                   walks block with regex, removes all lld_ip_reg_field_* │
  │                   including preceding /** @brief */ doxygen comment      │
  │                                                                          │
  │ FIELD_ADDED     → generate_field_functions() → template appended        │
  │                   then if LLM available: _llm_regenerate() enhances     │
  │                                                                          │
  │ BITWIDTH_CHANGED→ _regen_access_template(block, cr)                     │
  │                   remove old fns + regenerate with new return_type       │
  │                                                                          │
  │ ACCESS_CHANGED  → _regen_access_template(block, cr)                     │
  │                   remove old set→ add/remove getter/setter/clear         │
  │                                                                          │
  │ OFFSET_CHANGED  → _update_sha_anchor(block, new_sha) only               │
  │                   struct handles bit position — no body change needed    │
  │                                                                          │
  │ RESET_CHANGED   → _update_sha_anchor(block, new_sha) only               │
  │                   reset value not in C code                              │
  │                                                                          │
  │ COMMENT_CHANGED → _llm_regenerate(block, cr)                            │
  │                   LLM: rewrite /** @brief */ with new desc               │
  │                   Template fallback: _patch_doxygen_desc()              │
  │                                                                          │
  │ MULTI_CHANGED   → _llm_regenerate(block, cr)                            │
  │                   LLM: handle combined access + desc changes             │
  │                   Template fallback: desc patch + _regen_access_template │
  └──────────────────────────────────────────────────────────────────────────┘

Stage 4 — Tests
────────────────
write_test_file(new_ir, llm_client, lld_text)
  →  test_lld_pmu.c
     │
     For each field in new_ir:
       struct init pattern:
         SFR_PMU sfr = {0};
         struct lld_pmu lld = { .pSFR = &sfr };
       if llm_test_gen AND llm available:
         LLMClient.generate_test()  → rich struct-based C test stubs
         fallback to template if LLM fails
       else:
         generate_test_for_field()  → template struct-based test stubs
     │
     All stubs → main() calls each test function
```

### 3.4 SFR File Auto-Detection

```
SfrParser._detect_format(text):
    if "typedef volatile union" in text:
        → Samsung union-bitfield parser
        reads stNative struct fields with // lsb-msb [ACCESS] comment
    else:
        → #define MASK/SHIFT parser
        reads #define IP_REG_FIELD_MASK / _SHIFT / _ACCESS

IP auto-detection from filename:
    sfr_pmu.h         → IP = "PMU"
    sfr_uart_old.h    → IP = "UART"
    sfr_dma_v2.h      → IP = "DMA"
    Pattern: sfr_{ip}[_{suffix}].h  (case insensitive)
```

---

## 4. Struct-Based LLD Architecture

### 4.1 Core principle

Every LLD function uses a **struct pointer** to the LLD driver object, which holds a pointer to the SFR memory-mapped struct. There are **no raw masks, no bit shifts, no base[] arrays** in generated code.

```c
/* ─── SFR aggregate struct (generated in lld_pmu.h header) ─── */
typedef volatile struct {
    SFR_PMU_PMU_CTRL  stPMU_CTRL;    /* 0x0000 */
    SFR_PMU_STATUS    stSTATUS;      /* 0x0004 */
    SFR_PMU_CLK_CON   stCLK_CON;    /* 0x0008 */
    SFR_PMU_RST_CON   stRST_CON;    /* 0x000C */
    SFR_PMU_IRQ_CON   stIRQ_CON;    /* 0x0010 */
} SFR_PMU, *pSFR_PMU;

/* ─── LLD driver object ─── */
struct lld_pmu {
    pSFR_PMU pSFR;    /* pointer to memory-mapped SFR block */
};
```

### 4.2 Function template — Getter (RO / RW)

```c
/** @brief Clock source mux: 0=PLL0 1=PLL1 2=OSC 3=EXT */
static inline uint8_t lld_pmu_clk_con_clk_sel_get(struct lld_pmu *lld)
{
    return (uint8_t)(lld->pSFR->stCLK_CON.stNative.CLK_SEL);
}
```

### 4.3 Function template — Setter (RW)

```c
/** @brief Clock source mux: 0=PLL0 1=PLL1 2=OSC 3=EXT */
static inline void lld_pmu_clk_con_clk_sel_set(struct lld_pmu *lld, uint8_t val)
{
    lld->pSFR->stCLK_CON.stNative.CLK_SEL = val;
}
```

### 4.4 Function template — W1C Clear

```c
/** @brief DMA done flag — write 1 to clear */
static inline void lld_pmu_status_complete_clear(struct lld_pmu *lld)
{
    lld->pSFR->stSTATUS.stNative.COMPLETE = 1U; /* W1C */
}
```

### 4.5 Access path formula

```
lld->pSFR->st{REG}.stNative.{FIELD}
             │          │       └─ bitfield member name (hardware name)
             │          └─ stNative = inner union of bitfield members
             └─ st{REG} = struct member name (st prefix + register name)
```

### 4.6 Naming rules — frozen function names

| Event | Function name | Body |
|-------|--------------|------|
| Normal | `lld_{ip}_{reg}_{field}_{verb}` | `lld->pSFR->st{REG}.stNative.{FIELD}` |
| REG_RENAMED (old→new) | **FROZEN** (old name kept) | Updated: `st{NEW_REG}` |
| FIELD_RENAMED (old→new) | **FROZEN** (old name kept) | Updated: `.stNative.{NEW_FIELD}` |
| FIELD_DELETED | Functions removed entirely | — |
| REG_DELETED | Functions **KEPT + DEPRECATED** | Not changed |

> **Key rule:** Function names never change after initial generation. This prevents compilation errors in IP emulation files that call these functions. The PR description lists deprecated functions for manual cleanup.

### 4.7 Unit test struct pattern

```c
/* Struct-based test mock — no raw register arrays */
static void test_lld_pmu_clk_con_clk_sel_get(void) {
    SFR_PMU sfr = {0};
    struct lld_pmu lld = { .pSFR = &sfr };

    sfr.stCLK_CON.stNative.CLK_SEL = 1U;       /* inject value */
    assert(lld_pmu_clk_con_clk_sel_get(&lld) == 1U);
}

static void test_lld_pmu_clk_con_clk_sel_set(void) {
    SFR_PMU sfr = {0};
    struct lld_pmu lld = { .pSFR = &sfr };

    lld_pmu_clk_con_clk_sel_set(&lld, 1U);
    assert(sfr.stCLK_CON.stNative.CLK_SEL == 1U);
}
```

---

## 5. How to Use — Template Mode (No LLM)

> **Template mode** is deterministic. No API token needed. All 12 change types handled by code transformations. COMMENT_CHANGED and MULTI_CHANGED use template fallback (regenerate from new FieldIR).

### 5.1 One-time setup

```powershell
git clone https://github.com/mnsbharadwaj/rtl_updates_openai
cd rtl_updates_openai/sfr_gen
pip install pyyaml
python -m lld_gen.main_sfr --help
```

### 5.2 Configure `lld_patcher.yaml` for template mode

```yaml
sfr_old_dir: "./old_sfr"
sfr_new_dir: "./new_sfr"
lld_dir:     "./lld"
output_dir:  "./lld_patched"
tests_dir:   "./tests_generated"

# LLM — DISABLED
no_llm:       true
hf_token:     ""
ollama_model: ""
llm_test_gen: false

no_git:     true
github_url: "https://github.com/yourorg/yourrepo"
gcc: null
```

### 5.3 Run

```powershell
python -m lld_gen.main_sfr run-config --config lld_patcher.yaml
```

**Expected output:**
```
[DISCOVER] Found 1 IP(s) to process:
  PMU          sfr_pmu.h -> sfr_pmu.h -> lld_pmu.h

  [1/4] Diff & Classify PMU...  Total changes: 12
  [2/4] Patch PMU LLD...
  [REG_RENAMED] PMU_CON.      ← fn names frozen, body updated
  [FIELD_ADDED] STATUS.ABORT  ← template generated
  [LLM-SKIP] STATUS.ABORT — using template fallback
  [COMMENT_CHANGED] CLK_CON.CLK_SEL
  [LLM-SKIP] CLK_CON.CLK_SEL — using template fallback
  [DEPRECATE] Register block: SWT_CON  ← kept + deprecated
  [ADD] Register block: IRQ_CON
  Test file written -> tests_generated/test_lld_pmu.c  [Template]

  [3/4] Compile-check PMU...  [OK]
  [4/4] Stage PR for PMU...   PR_DESCRIPTION.md written
```

### 5.4 Step-by-step CLI (without LLM)

```powershell
# Inspect changes only
python -m lld_gen.main_sfr diff --old old_sfr/sfr_pmu.h --new new_sfr/sfr_pmu.h

# Single-file patch
python -m lld_gen.main_sfr run `
    --old old_sfr/sfr_pmu.h --new new_sfr/sfr_pmu.h `
    --lld lld/lld_pmu.h --no-llm
```

---

## 6. How to Use — Ollama LLM Mode (Local)

> **Ollama mode** uses a locally running model — zero API cost, works offline. Tested with `qwen2.5-coder:1.5b`.

### 6.1 Install and start Ollama

```powershell
# Download Ollama from https://ollama.com/download
# Then pull the model:
ollama pull qwen2.5-coder:1.5b

# Start the server (runs on port 11434)
ollama serve
```

### 6.2 Configure `lld_patcher.yaml` for Ollama

```yaml
# LLM settings — Ollama local mode
no_llm:       false                  # enable LLM
ollama_model: "qwen2.5-coder"        # auto-matches qwen2.5-coder:1.5b
hf_token:     ""                     # not needed for Ollama

# LLM unit test generation
llm_test_gen: true                   # LLM writes richer struct-based tests
```

### 6.3 Run

```powershell
python -m lld_gen.main_sfr run-config --config lld_patcher.yaml
```

**With Ollama enabled, terminal shows:**
```
[LLM] Backend: Ollama (qwen2.5-coder:1.5b)

  [2/4] Patch PMU LLD...
  [COMMENT_CHANGED] CLK_CON.CLK_SEL
  [LLM-OLLAMA] Generating: CLK_CON.CLK_SEL (COMMENT_CHANGED) …
  [MULTI_CHANGED] CLK_CON.CLK_GATE
  [LLM-OLLAMA] Generating: CLK_CON.CLK_GATE (MULTI_CHANGED) …

  Test file written -> tests_generated/test_lld_pmu.c  [LLM+Template fallback]
  [LLM-TEST-OLLAMA] Generating tests: CLK_CON.CLK_SEL (RW) …
```

### 6.4 Which changes use LLM?

| Change Type | LLM for LLD patch? | LLM for tests? |
|-------------|-------------------|----------------|
| `REG_RENAMED` | No — freeze name, patch body | Template |
| `REG_DELETED` | No — deprecate block | Not applicable |
| `REG_ADDED` | Template block; LLM optional | LLM (if llm_test_gen) |
| `FIELD_RENAMED` | No — freeze name, patch body | Template |
| `FIELD_DELETED` | No — remove fns | Not applicable |
| `FIELD_ADDED` | Template first; LLM enhances if available | LLM (if llm_test_gen) |
| `BITWIDTH_CHANGED` | No — regen template | LLM (if llm_test_gen) |
| `ACCESS_CHANGED` | No — regen template | LLM (if llm_test_gen) |
| `OFFSET_CHANGED` | No — SHA update only (struct handles bit pos) | LLM (if llm_test_gen) |
| `RESET_CHANGED` | No — SHA update only | LLM (if llm_test_gen) |
| `COMMENT_CHANGED` | **Yes** — LLM rewrites docstring | LLM (if llm_test_gen) |
| `MULTI_CHANGED` | **Yes** — LLM handles combined | LLM (if llm_test_gen) |

### 6.5 LLM caching

Responses cached in `.lld_gen_cache/` by SHA-16 of `(reg|field|change_type|desc)`:
- Same SFR change → same cache key → no API call on re-run
- Delete `.lld_gen_cache/` to force fresh LLM calls

### 6.6 Per-IP Ollama override

```yaml
no_llm: true   # global default: template mode

ip_overrides:
  PMU:
    no_llm: false
    ollama_model: "qwen2.5-coder"   # Ollama for PMU only
  UART:
    no_llm: true   # keep template for UART
```

---

## 7. How to Use — HuggingFace LLM Mode (Cloud)

> Uses **Qwen2.5-Coder-7B-Instruct** via HuggingFace Inference API. Requires HF account + token.

### 7.1 Get a HuggingFace token

1. Go to **https://huggingface.co/settings/tokens**
2. Click **New Token** → Role: **Read**
3. Copy: `hf_xxxxxxxxxxxxxxxxxxxxxxxxx`

### 7.2 Configure for HuggingFace

```yaml
no_llm:       false
ollama_model: ""                          # leave empty → use HuggingFace
hf_token:     ""                          # or set HF_TOKEN env var
llm_test_gen: true
```

```powershell
$env:HF_TOKEN = "hf_xxxxxxxxxxxxxxxxxxxxxxxxx"
python -m lld_gen.main_sfr run-config --config lld_patcher.yaml
```

### 7.3 Backend priority

```
LLMClient._detect_backend():
  1. ollama_model set AND Ollama reachable  →  "ollama"   ✅ (preferred)
  2. hf_token set                           →  "huggingface"
  3. neither                                →  "none"  (template fallback)
```

---

## 8. Config File Reference

```yaml
# ============================================================
# lld_patcher.yaml — LLD Auto-Patcher Configuration v2.0
# ============================================================

# ── Directories ──────────────────────────────────────────────
sfr_old_dir: "./old_sfr"          # Old SFR headers (read-only)
sfr_new_dir: "./new_sfr"          # New/updated SFR headers (read-only)
lld_dir:     "./lld"              # Original LLD files (NEVER modified)
output_dir:  "./lld_patched"      # Patched LLD written here
tests_dir:   "./tests_generated"  # Unit test .c files written here

# ── LLM settings ─────────────────────────────────────────────
no_llm:       false               # false = use LLM backend (default: false)
                                  # true  = template only (no API calls)

# Ollama (local, zero-cost) — takes priority if reachable
ollama_model: "qwen2.5-coder"     # partial name OK; auto-matches :1.5b tags
                                  # Leave empty "" to skip Ollama

# HuggingFace (cloud, requires token) — fallback if Ollama not set
hf_token: ""                      # or set HF_TOKEN environment variable
                                  # Get from: huggingface.co/settings/tokens

# ── Unit test mode ────────────────────────────────────────────
llm_test_gen: true                # true  = LLM writes richer struct-based tests
                                  # false = fast deterministic template tests

# ── Git / PR settings ─────────────────────────────────────────
no_git:       true                # false = git add all output files
github_url:   ""                  # GitHub repo URL for PR creation link
                                  # e.g. https://github.com/org/repo

# ── Compiler ─────────────────────────────────────────────────
gcc: null                         # null = auto-detect from PATH
                                  # Windows: choco install mingw

# ── IP filtering (optional) ──────────────────────────────────
# ip_list:
#   - PMU
#   - UART

# ── Per-IP overrides (optional) ───────────────────────────────
# ip_overrides:
#   PMU:
#     no_llm: false
#     ollama_model: "qwen2.5-coder"
#   UART:
#     no_llm: true
```

---

## 9. Traceability Matrix — 12 Change Types

### Type 1 — `REG_RENAMED`

| Item | Detail |
|------|--------|
| **Detection** | Same SHA-16 fingerprint (same fields), different register name |
| **Patch strategy** | `_patch_struct_member_ref()` — updates `lld->pSFR->stOLD.` → `lld->pSFR->stNEW.` in function bodies |
| **Function names** | **FROZEN** — `lld_pmu_pmu_con_*` kept as-is (no rename) |
| **SRC_SHA updated?** | Yes |
| **LLM needed?** | No |
| **Example** | `PMU_CON` → `PMU_CTRL`: body gets `->stPMU_CTRL.stNative.DMA_EN`, name stays `lld_pmu_pmu_con_dma_en_get` |

### Type 2 — `REG_DELETED`

| Item | Detail |
|------|--------|
| **Detection** | Register exists in old IR, absent in new IR; no matching SHA found |
| **Patch strategy** | `_deprecate_block()` — adds `/* ⚠ DEPRECATED: ... */` comment; functions **KEPT** |
| **Function names** | Preserved in file; listed in `PR_DESCRIPTION.md` for manual deletion |
| **SRC_SHA updated?** | N/A — block annotated, not removed |
| **LLM needed?** | No |
| **Example** | `SWT_CON` removed; `lld_pmu_swt_con_*` functions kept with DEPRECATED header |

### Type 3 — `REG_ADDED`

| Item | Detail |
|------|--------|
| **Detection** | Register in new IR, absent in old IR |
| **Patch strategy** | `generate_register_block()` → struct-based block appended before `#endif` |
| **Function names** | Fresh: `lld_{ip}_{reg}_{field}_{verb}` |
| **SRC_SHA updated?** | New SHA inserted |
| **Body pattern** | `lld->pSFR->stIRQ_CON.stNative.IRQ_EN = val;` |
| **LLM needed?** | Optional — template generated first; LLM enhances if available |
| **Example** | `IRQ_CON` added with `IRQ_EN`, `IRQ_PEND`, `IRQ_MASK` fields |

### Type 4 — `FIELD_RENAMED`

| Item | Detail |
|------|--------|
| **Detection** | Same mask + shift + access, different field name |
| **Patch strategy** | `_patch_field_name_in_body()` — updates `.stNative.OLD` → `.stNative.NEW` in body |
| **Function names** | **FROZEN** — `lld_pmu_status_done_*` kept as-is |
| **SRC_SHA updated?** | Yes |
| **LLM needed?** | No |
| **Example** | `STATUS.DONE` → `STATUS.COMPLETE`: body gets `.stNative.COMPLETE`, fn stays `_done_` |

### Type 5 — `FIELD_DELETED`

| Item | Detail |
|------|--------|
| **Detection** | Field in old IR, absent in new IR (same register) |
| **Patch strategy** | `_remove_field_functions_raw()` — removes all `lld_ip_reg_field_*` fns + doxygen |
| **Function names** | Removed from file |
| **SRC_SHA updated?** | Yes |
| **LLM needed?** | No |
| **Example** | `STATUS_CON.FAIL` removed — `lld_pmu_status_con_fail_get()` deleted |

### Type 6 — `FIELD_ADDED`

| Item | Detail |
|------|--------|
| **Detection** | Field in new IR, absent in old IR (same register) |
| **Patch strategy** | `generate_field_functions()` appended to block (template **always**); LLM enhances if available |
| **Function names** | Fresh: `lld_pmu_status_con_abort_get()` |
| **Body** | `lld->pSFR->stSTATUS_CON.stNative.ABORT` |
| **SRC_SHA updated?** | Yes |
| **LLM needed?** | Optional (template first, LLM second) |
| **Example** | `STATUS_CON.ABORT` added at bit[9], RW |

### Type 7 — `BITWIDTH_CHANGED`

| Item | Detail |
|------|--------|
| **Detection** | `popcount(old_mask) ≠ popcount(new_mask)` |
| **Patch strategy** | `_regen_access_template()` — remove old fns, regenerate with new return_type |
| **Struct advantage** | No mask/shift in body — hardware struct handles it; only return_type changes |
| **LLM needed?** | No |
| **Example** | `STATUS_CON.THRESH` [6:3] 4-bit → [7:3] 5-bit; return_type stays uint8_t |

### Type 8 — `ACCESS_CHANGED`

| Item | Detail |
|------|--------|
| **Detection** | Same mask + shift, different access string |
| **Patch strategy** | `_regen_access_template()` — regenerate correct fn set for new access type |
| **LLD functions** | `RW` (get+set) → `RO` (get only, setter removed) |
| **LLM needed?** | No |
| **Example** | `STATUS_CON.ERR` RO → RW; getter kept, setter added |

### Type 9 — `OFFSET_CHANGED`

| Item | Detail |
|------|--------|
| **Detection** | Same mask width, different `shift` (lsb position) |
| **Patch strategy** | **SHA update only** — struct-based bodies have NO raw shifts; hardware handles bit position |
| **LLD functions** | Unchanged (struct member reference is position-independent) |
| **LLM needed?** | No |
| **Example** | `STATUS_CON.LEVEL` [8:7] → [10:9]: body `->stNative.LEVEL` unchanged |

### Type 10 — `RESET_CHANGED`

| Item | Detail |
|------|--------|
| **Detection** | Register `_VALUE_` macro or reset field changed |
| **Patch strategy** | SHA update only (reset value not encoded in C function bodies) |
| **LLD functions** | No change |
| **LLM needed?** | No (for LLD); Optional (for test: LLM can add reset-value assertion) |
| **Example** | `CLK_CON` reset 0x0 → 0x0E |

### Type 11 — `COMMENT_CHANGED`

| Item | Detail |
|------|--------|
| **Detection** | Same mask + shift + access, different `desc` field |
| **Patch strategy** | LLM rewrites `/** @brief */` docstring; template fallback: `_patch_doxygen_desc()` |
| **LLM needed?** | **Yes** (for LLD docstring) |
| **Example** | `CLK_CON.CLK_SEL`: `"Clock source select signal"` → `"Clock source mux: 0=PLL0 1=PLL1 2=OSC 3=EXT"` |

### Type 12 — `MULTI_CHANGED`

| Item | Detail |
|------|--------|
| **Detection** | Two or more attributes changed in same field |
| **Patch strategy** | LLM handles combined; template fallback: access regen + desc patch |
| **LLD functions** | Both access and docstring updated; setter removed if now RO |
| **LLM needed?** | **Yes** (for LLD) |
| **Example** | `CLK_CON.CLK_GATE`: RW → RO **and** desc changed |

---

## 10. Unit Test Catalog

### 10.1 Template-generated tests (struct-based)

#### RW field — getter + setter

```c
static void test_lld_pmu_clk_con_clk_sel_get(void) {
    SFR_PMU sfr = {0};
    struct lld_pmu lld = { .pSFR = &sfr };
    sfr.stCLK_CON.stNative.CLK_SEL = 1U;
    assert(lld_pmu_clk_con_clk_sel_get(&lld) == 1U);
}

static void test_lld_pmu_clk_con_clk_sel_set(void) {
    SFR_PMU sfr = {0};
    struct lld_pmu lld = { .pSFR = &sfr };
    lld_pmu_clk_con_clk_sel_set(&lld, 1U);
    assert(sfr.stCLK_CON.stNative.CLK_SEL == 1U);
}
```

#### RO field — getter only

```c
static void test_lld_pmu_pmu_ctrl_dma_pass_get(void) {
    SFR_PMU sfr = {0};
    struct lld_pmu lld = { .pSFR = &sfr };
    sfr.stPMU_CTRL.stNative.DMA_PASS = 1U;
    assert(lld_pmu_pmu_ctrl_dma_pass_get(&lld) == 1U);
}
/* No setter test — RO has no set function */
```

#### W1C field — getter + clear

```c
static void test_lld_pmu_status_complete_get(void) {
    SFR_PMU sfr = {0};
    struct lld_pmu lld = { .pSFR = &sfr };
    sfr.stSTATUS.stNative.COMPLETE = 1U;
    assert(lld_pmu_status_complete_get(&lld) == 1U);
}

static void test_lld_pmu_status_complete_clear(void) {
    SFR_PMU sfr = {0};
    struct lld_pmu lld = { .pSFR = &sfr };
    lld_pmu_status_complete_clear(&lld);
    assert(sfr.stSTATUS.stNative.COMPLETE == 1U);  /* W1C: writes 1 */
}
```

### 10.2 Template test coverage

| Access | `_get` | `_set` | `_clear` | `_set1` |
|--------|--------|--------|----------|---------|
| RW | ✅ | ✅ | — | — |
| RO | ✅ | — | — | — |
| WO | — | ✅ | — | — |
| W1C | ✅ | — | ✅ | — |
| W1S | ✅ | — | — | ✅ |

### 10.3 LLM-generated tests (`llm_test_gen: true`)

The LLM receives the struct access pattern and reference implementation:

```
System: "Write static void test functions using:
         SFR_{IP} sfr = {{0}};
         struct lld_{ip} lld = {{ .pSFR = &sfr }};
         Test via struct member: sfr.st{REG}.stNative.{FIELD}"

User:   IP: PMU
        Register: CLK_CON  struct member: stCLK_CON
        Field: CLK_SEL  access=RW  width=2 bits
        Description: Clock source mux: 0=PLL0 1=PLL1 2=OSC 3=EXT
        Max value: 0x3

        Functions to test:
          - lld_pmu_clk_con_clk_sel_get
          - lld_pmu_clk_con_clk_sel_set
```

LLM produces (example):
```c
static void test_lld_pmu_clk_con_clk_sel_get_normal(void) {
    SFR_PMU sfr = {0};
    struct lld_pmu lld = { .pSFR = &sfr };
    sfr.stCLK_CON.stNative.CLK_SEL = 2U;   /* OSC */
    assert(lld_pmu_clk_con_clk_sel_get(&lld) == 2U);
}

static void test_lld_pmu_clk_con_clk_sel_set_max(void) {
    SFR_PMU sfr = {0};
    struct lld_pmu lld = { .pSFR = &sfr };
    lld_pmu_clk_con_clk_sel_set(&lld, 3U);  /* EXT, max=3 for 2-bit */
    assert(sfr.stCLK_CON.stNative.CLK_SEL == 3U);
}
```

---

## 11. LLM Prompt Reference

### 11.1 LLD function generation system prompt (struct-based)

```
You are an expert embedded C firmware engineer for Samsung SFR register drivers.
Generate ONLY raw C code with these rules:
- No #include, no #define, no markdown fences, no prose
- static inline functions only
- Parameter: struct lld_{ip} *lld   (replace {ip} with actual IP, lowercase)
- Access bitfields via: lld->pSFR->st{REG}.stNative.{FIELD}
- Getter: return ({return_type})(lld->pSFR->stREG.stNative.FIELD);
- Setter: lld->pSFR->stREG.stNative.FIELD = val;
- W1C clear: lld->pSFR->stREG.stNative.FIELD = 1U; /* W1C */
- NO raw masks (0x...), NO bit shifts (>>), NO base[] arrays
- Function naming: lld_{ip}_{reg}_{field}_{verb}  all lowercase
- Add /** @brief {desc} */ doxygen comment before each function
```

### 11.2 LLD function user prompt

```
IP: PMU  (lowercase struct: struct lld_pmu *lld)
Register: CLK_CON  struct member: stCLK_CON
Field: CLK_SEL  access=RW
Field description: Clock source mux: 0=PLL0 1=PLL1 2=OSC 3=EXT
Return type: uint8_t
Change type: COMMENT_CHANGED

Required functions:
  - lld_pmu_clk_con_clk_sel_get
  - lld_pmu_clk_con_clk_sel_set

Access path: lld->pSFR->stCLK_CON.stNative.CLK_SEL

Old function text (for context):
/** @brief Clock source select signal */
static inline uint8_t lld_pmu_clk_con_clk_sel_get(struct lld_pmu *lld)
{
    return (uint8_t)(lld->pSFR->stCLK_CON.stNative.CLK_SEL);
}

Generate the updated C static inline function(s) — struct-based, no masks.
```

### 11.3 Unit test system prompt (struct-based)

```
You are an expert embedded C firmware test engineer for Samsung SFR drivers.
Generate ONLY raw C code — no prose, no markdown fences, no #include lines.
Write static void test functions using:
  SFR_{IP} sfr = {{0}};
  struct lld_{ip} lld = {{ .pSFR = &sfr }};
Rules:
  - Test via struct member:  sfr.st{REG}.stNative.{FIELD}
  - For RW: set via lld function, assert sfr member equals value
  - For RO: only getter test (inject into sfr, read via lld)
  - For W1C: call clear(), assert member == 1U
  - For W1S: call set1(), assert member == 1U
  - One test function per behaviour: test_{fn_name}_{scenario}()
```

### 11.4 Compile-error fix prompt

```
Broken code:
```c
{broken_code}
```

GCC error:
```
{error_msg}
```

Return the fixed C function(s) — struct-based, no masks, no shifts.
```

---

## 12. SRC_SHA Anchor Mechanism

### Purpose

Allows the patcher to find which block belongs to which register, even after manual edits, and detect unchanged blocks for zero-modification guarantee.

### Format in `lld_pmu.h`

```c
/* ═══════════════════════════════════════════════════════════════
 * REGISTER: STATUS_CON                     offset=0x0004
 * DMA Status Control
 * SRC_SHA: 29f32a36c9e24472
 * ═══════════════════════════════════════════════════════════════ */

/** @brief DMA done flag */
static inline uint8_t lld_pmu_status_con_complete_get(struct lld_pmu *lld)
{
    return (uint8_t)(lld->pSFR->stSTATUS_CON.stNative.COMPLETE);
}
/* ... more functions ... */

/* next SHA block or #endif */
```

### SHA-16 Computation

```python
def _sha16(reg: RegisterIR) -> str:
    payload = json.dumps({
        "name":   reg.name,
        "offset": reg.offset,
        "fields": {
            fname: {
                "mask":   f.mask,
                "shift":  f.shift,
                "access": f.access,
                "desc":   f.desc,
            }
            for fname, f in sorted(reg.fields.items())
        }
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
```

### Block split and reassembly

```python
# _split_blocks() returns:
[
    (None, None, "/* file header + struct defs */\n#ifndef LLD_PMU_H\n..."),
    ("STATUS_CON", "29f32a36c9e24472", "/* ════...STATUS_CON...*/ \nstatic inline..."),
    ("CLK_CON",    "eb08a01c1bdf1630", "/* ════...CLK_CON...  */\nstatic inline..."),
    (None, None, "\n#endif /* LLD_PMU_H */\n"),
]
# After patching: reassemble with updated blocks + new SHA
```

---

## 13. LLM vs Template Comparison Tool

`compare_llm_vs_template.py` runs both generation modes on the same SFR pair and produces a side-by-side diff report.

### Usage

```powershell
# Start Ollama first
ollama serve

# Run comparison
python compare_llm_vs_template.py --ip PMU --model qwen2.5-coder
```

### Output

```
[1/2] Generating template output...  7948 chars
[2/2] Generating LLM output (Ollama)...  8124 chars

  [ 1] REG_RENAMED            PMU_CON       -            fn:✅ path:✅ masks:✅ → MATCH
  [ 2] FIELD_DELETED          STATUS_CON    FAIL         fn:✅ path:✅ masks:✅ → MATCH
  [ 3] FIELD_ADDED            STATUS_CON    ABORT        fn:✅ path:✅ masks:✅ → MATCH
  ...
  [11] COMMENT_CHANGED        CLK_CON       CLK_SEL      fn:✅ path:✅ masks:✅ → MATCH
  [12] MULTI_CHANGED          CLK_CON       CLK_GATE     fn:✅ path:✅ masks:✅ → MATCH
```

Report saved to `llm_vs_template_report.md` with:
- Per-change structural analysis (function names, struct paths, no-mask check)
- Unified diff for each register block
- Full file diff (template → LLM)

---

## 14. Quick Reference Card

```
┌─────────────────────────────────────────────────────────────────────────┐
│              LLD Auto-Patcher v2.0 Quick Reference                      │
├─────────────────────────────────────────────────────────────────────────┤
│  SETUP                                                                  │
│    pip install pyyaml                                                   │
│    python -m lld_gen.main_sfr init-config                              │
│                                                                         │
│  TEMPLATE MODE (no LLM, offline, deterministic)                        │
│    lld_patcher.yaml: no_llm: true, llm_test_gen: false                 │
│    python -m lld_gen.main_sfr run-config --config lld_patcher.yaml     │
│                                                                         │
│  OLLAMA MODE (local, zero-cost, rich docstrings + tests)               │
│    ollama pull qwen2.5-coder:1.5b && ollama serve                      │
│    lld_patcher.yaml: no_llm: false, ollama_model: qwen2.5-coder        │
│    python -m lld_gen.main_sfr run-config --config lld_patcher.yaml     │
│                                                                         │
│  HUGGINGFACE MODE (cloud, Qwen2.5-Coder-7B)                           │
│    $env:HF_TOKEN = "hf_xxxx"                                           │
│    lld_patcher.yaml: no_llm: false, ollama_model: ""                   │
│    python -m lld_gen.main_sfr run-config --config lld_patcher.yaml     │
│                                                                         │
│  STEP-BY-STEP CLI                                                       │
│    python -m lld_gen.main_sfr diff  --old X --new Y                    │
│    python -m lld_gen.main_sfr patch --old X --new Y --lld L            │
│    python -m lld_gen.main_sfr run   --old X --new Y --lld L            │
│                                                                         │
│  LLM vs TEMPLATE COMPARISON                                             │
│    python compare_llm_vs_template.py --ip PMU --model qwen2.5-coder    │
│    → produces: llm_vs_template_report.md                               │
│                                                                         │
│  OUTPUT FILES                                                           │
│    lld_patched/lld_pmu.h           ← patched struct-based LLD          │
│    tests_generated/test_lld_pmu.c  ← struct-init unit tests            │
│    lld_patched/PR_DESCRIPTION.md   ← change table + deprecated fn list │
│                                                                         │
│  FUNCTION NAMING (frozen after first generation)                        │
│    lld_{ip}_{reg}_{field}_{verb}   all lowercase                       │
│    lld_pmu_status_con_thresh_get(&lld)                                 │
│    lld_pmu_status_con_thresh_set(&lld, val)                            │
│    lld_pmu_status_con_complete_clear(&lld)                             │
│                                                                         │
│  STRUCT ACCESS PATTERN                                                  │
│    lld->pSFR->st{REG}.stNative.{FIELD}                                │
│    lld->pSFR->stSTATUS_CON.stNative.THRESH                            │
│    — no raw masks, no shifts, no base[] arrays                         │
│                                                                         │
│  DEPRECATION POLICY                                                     │
│    REG_DELETED → functions KEPT + DEPRECATED comment                   │
│    → listed in PR_DESCRIPTION.md for manual deletion                   │
│    → avoids compilation errors in IP emulation files                   │
└─────────────────────────────────────────────────────────────────────────┘
```
