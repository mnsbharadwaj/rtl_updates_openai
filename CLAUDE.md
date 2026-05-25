# CLAUDE.md — LLD Auto-Patcher Agent Workflow Orchestrator

> **Priority**: This file takes priority over all inline code comments in case of conflict.

---

## Project Identity

**Tool**: `lld_gen` pipeline (Python 3.11 + Qwen2.5-Coder-7B cloud)  
**Purpose**: Automated Low-Level Driver generation from SFR header diffs  
**Organisation**: Samsung Semiconductor Research India (SSRI)  
**Division**: CXL Architecture & Security IP Division

---

## Repository Layout

```
sfr_gen/
├── lld_gen/
│   ├── __init__.py
│   ├── main_sfr.py          CLI: diff / patch / compile-check / stage / run
│   ├── sfr_diff_analyzer.py SFR parser + 12-type classifier
│   ├── lld_patcher.py       SRC_SHA anchor patcher + template/LLM dispatcher
│   ├── llm_client.py        Qwen2.5-Coder-7B via HuggingFace Inference API
│   ├── compile_check.py     gcc -fsyntax-only loop + LLM fix retry
│   └── pr_stage.py          PR_DESCRIPTION.md + git add
├── tests/
│   └── test_sfr_diff.py     43-test pytest suite (all change types)
├── CLAUDE.md                ← YOU ARE HERE
└── SKILL.md                 Coding conventions (function templates, naming)
```

---

## SFR Comment Format (Single Source of Truth)

Every field in `sfr.h` must have a structured comment immediately above its MASK define:

```c
/* FIELDNAME [MSB:LSB] ACCESS — description */
#define PERIPH_REG_FIELDNAME_MASK   0x000000XXU
#define PERIPH_REG_FIELDNAME_SHIFT  XU
```

**Parser regex**:
```python
r'/\* (\w+) \[(\d+):(\d+)\] (\w+)\s*[—\-]{1,3}\s*(.*?) \*/'
```

- `FIELDNAME` → field name (must match MASK/SHIFT define suffix)
- `[MSB:LSB]` → bit positions (MSB ≥ LSB ≥ 0)
- `ACCESS` → one of: `RO`, `RW`, `WO`, `W1C`, `W1S`
- `description` → free text; drives LLM context and docstrings

---

## Four-Step Pipeline

Run the **full pipeline** with a single command:

```bash
export HF_TOKEN=hf_xxxx
python -m lld_gen.main_sfr run \
    --old sfr_old.h --new sfr_new.h \
    --lld lld.h --ip DMA
```

Or step by step:

```bash
# Step 1: Classify changes
python -m lld_gen.main_sfr diff \
    --old sfr_old.h --new sfr_new.h \
    --ip DMA --classify-out changes.json

# Step 2: Patch lld.h
python -m lld_gen.main_sfr patch \
    --changes changes.json --lld lld.h \
    --sfr-new sfr_new.h --ip DMA

# Step 3: Compile verification
python -m lld_gen.main_sfr compile-check \
    --sfr sfr_new.h --lld lld.h \
    --tests tests/test_lld_generated.c

# Step 4: Stage PR
python -m lld_gen.main_sfr stage \
    --old-sfr sfr_old.h --new-sfr sfr_new.h \
    --lld lld.h --ip DMA
```

---

## 12 Change Types — Classification Priority

The classifier applies rules in strict priority order. **First match wins.**

| Priority | Change Type | Detection | LLM? |
|----------|-------------|-----------|------|
| 1 | `REG_RENAMED` | Same byte offset, different name | No |
| 2 | `REG_DELETED` | OFFSET define gone from new sfr.h | No |
| 3 | `REG_ADDED` | New OFFSET define appears | Cond |
| 4 | `FIELD_RENAMED` | Same mask+shift, different name | No |
| 5 | `FIELD_DELETED` | MASK define gone from new sfr.h | No |
| 6 | `FIELD_ADDED` | New MASK define appears | Cond |
| 7 | `BITWIDTH_CHANGED` | popcount(old_mask) ≠ popcount(new_mask) | No |
| 8 | `ACCESS_CHANGED` | ACCESS token changed | No |
| 9 | `OFFSET_CHANGED` | field shift (lsb) changed | No |
| 10 | `RESET_CHANGED` | reset value changed | No |
| 11 | `COMMENT_CHANGED` | description text changed | **Yes** |
| 12 | `MULTI_CHANGED` | 2+ non-priority changes in same field | **Yes** |

**LLM routing rules**:
- LLM = **Yes**: always call Qwen2.5-Coder-7B
- LLM = **Cond**: call LLM only if field description is non-empty
- LLM = **No**: deterministic template transformation only

---

## LLM Configuration

- **Model**: `Qwen/Qwen2.5-Coder-7B-Instruct`
- **API**: HuggingFace Inference API
- **Endpoint**: `https://api-inference.huggingface.co/models/Qwen/Qwen2.5-Coder-7B-Instruct`
- **Auth**: `Authorization: Bearer $HF_TOKEN`
- **Token budget**: `max_new_tokens=512` per call
- **Temperature**: `0.1` (near-deterministic)
- **Retry policy**: up to 3 retries on API error (exponential backoff 1s, 2s, 4s)
- **Cache**: `.lld_gen_cache/{sha16}.c` — unchanged fields never re-query
- **Fallback**: If `HF_TOKEN` not set → template-only generation (no LLM)

---

## Hard Invariants (never violate)

1. **Unchanged blocks must be bit-identical** — only blocks with a changed SRC_SHA are regenerated
2. **Every function must pass `gcc -fsyntax-only` before PR staging** — no invalid code reaches review
3. **LLM output is always gcc-validated** — LLM failure falls back to template generation
4. **Programmer code outside SRC_SHA blocks is never touched** — surgical patching only
5. **Token budget is enforced per call** — `max_new_tokens=512` hard limit

---

## SRC_SHA Anchor Format

Every register block in `lld.h` must have this exact header comment:

```c
/* ═══════════════════════════════════════════════════════════════
 * REGISTER: CTRL                     offset=0x0000
 * DMA Control Register
 * SRC_SHA: a1b2c3d4e5f6g7h8
 * ═══════════════════════════════════════════════════════════════ */
```

- SHA-16 = first 16 hex chars of `sha256(reg_name|offset|desc|field_shas)`
- The patcher locates blocks by matching `SRC_SHA:` + `REGISTER:` in the same comment
- Changed SHA → block is regenerated; same SHA → copied verbatim

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HF_TOKEN` | Yes (for LLM) | HuggingFace API token (`hf_xxxx`) |

---

## Running Tests

```bash
cd sfr_gen
pytest tests/test_sfr_diff.py -v
# Expected: 43 passed
```
