# SSRI LLD Auto-Patcher Release Notes — Version 2.1

We are pleased to release **Version 2.1** of the **Samsung SSRI LLD Auto-Patcher**. This update resolves parser limitations in the C AST refactoring engine, introduces a fallback mechanism for missing register blocks, and enhances observability with detailed function-level summaries at the end of runs.

---

## What's New in Version 2.1

### 1. Robust C AST Parsing & Custom Typedef Extraction
* **Robustness**: The cross-refactoring parser (`ast_refactor`) is now immune to parse errors caused by custom pointer definitions, hardware types, or non-standard types (e.g. `pSFR_HSATU`, `SFR_PCIELINK`, `uint32`, `u32`, etc.).
* **Dynamic Scans**: The parser dynamically scans all driver header files prior to parsing, extracts all local single-word and struct/union typedef declarations, and registers them dynamically in the lexer symbol table.

### 2. Missing Register Block Fallback
* **Fallback Appends**: When field-level modifications (like `FIELD_ADDED` or `BITWIDTH_CHANGED`) target a register that has no existing block in the LLD header, the patcher automatically generates and appends the entire register block (and all of its field functions) to the file.
* **Accuracy**: Prevents the patcher from silently skipping changes for new or previously unexposed registers.

### 3. File-Level LLD Function Summary
* **Granular Observability**: The console output at the end of a batch run now displays a detailed summary list of exactly which C/C++ functions were added, patched, or removed per LLD file.

---

## Brief User Guide

### 1. Installation & Setup
Ensure dependencies are installed:
```bash
pip install -r requirements.txt
```

*(Optional)* Install GCC for compiler checks:
* **Windows**:
  ```powershell
  winget install BrechtSanders.WinLibs.POSIX.UCRT --accept-source-agreements --accept-package-agreements
  ```
  *(Update the `gcc:` path in your `lld_patcher.yaml` to point to the installed `gcc.exe`)*

### 2. Command Reference

#### Running Batch Orchestration (Config-Driven)
Process all IPs and their respective LLD headers as defined in the YAML configuration:
```bash
python -m lld_gen.main_sfr run-config --config lld_patcher.yaml
```

#### Verbose Mode (Recommended for Auditing)
To see full prompt logs, LLM responses, and detailed file diffs during the run:
```bash
python -m lld_gen.main_sfr run-config --config lld_patcher.yaml --verbose
```

#### Offline/Template-Only Mode
To run completely locally and deterministically without making any LLM network/local calls:
```bash
python -m lld_gen.main_sfr run-config --config lld_patcher.yaml --no-llm
```

#### Single IP Run
Process a single IP block directly from the CLI:
```bash
python -m lld_gen.main_sfr run \
    --old old_sfr/sfr_pmu.h \
    --new new_sfr/sfr_pmu.h \
    --lld lld/lld_pmu.h \
    --ip PMU
```

### 3. Register Comment Invariant
Every field inside your SFR `.h` files must match this comment pattern for the parser to extract access rules:
```c
/* FIELDNAME [MSB:LSB] ACCESS — Description */
#define PERIPH_REG_FIELDNAME_MASK   0x000000XXU
#define PERIPH_REG_FIELDNAME_SHIFT  XU
```
Supported Access flags: `RW`, `RO`, `WO`, `W1C`, `W1S`.
