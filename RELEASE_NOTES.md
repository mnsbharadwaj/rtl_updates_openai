# SSRI LLD Auto-Patcher Release Notes — Version 2.0

We are pleased to release **Version 2.0** of the **Samsung SSRI LLD Auto-Patcher**. This version adds support for modern C++ codebases, integrates deep cross-file AST refactoring in batch operations, limits functional tests to patched logic, and implements smart optimization gates to reduce LLM overhead.

---

## What's New in Version 2.0

### 1. C++ Function Signature & Inline Compatibility
* **Flexibility**: The function parser and deprecation engine are now fully compatible with both C and C++ function signatures.
* **Support**: Correctly extracts and patches functions containing the `inline` keyword alone (without `static`), class member functions, or regular functions, and supports multi-token C/C++ return types (e.g. `unsigned int` or `volatile uint16_t`).

### 2. Batch Cross-File AST Refactoring
* **Propagating Changes**: When registers or fields are renamed/deleted, the changes are now automatically refactored in-place across **all** caller LLD files in the scan directory when running in config-driven `run-config` mode (reusing the AST-based pycparser cross-refactoring engine from the workflow mode).

### 3. Selective Functional Unit Test Generation
* **Reduced Clutter**: Instead of generating unit test assertions for the entire register map, the pipeline now generates functional test stubs **only for patched or added LLD functions**. This reduces compilation overhead and makes the test suite highly focused on the actual changes.

### 4. Reset Value Change Ignoring
* **Efficiency**: Reset value changes alone are now classified as cosmetic and ignored as functional changes. The diff engine will not generate patching tasks, run LLM calls, or produce PR change rows for pure reset value updates, preventing unnecessary LLM token cost.

### 5. Two-Stage Semantic Description Gate
* **Description Filtering**: Description updates (e.g. `COMMENT_CHANGED`) undergo a two-stage filter:
  1. **Heuristic string similarity**: Skips LLM calls if description similarity is above 85%.
  2. **Lightweight LLM YES/NO equivalence check**: For borderline cases (75%-85% similarity), queries the LLM with a small prompt to verify semantic difference before initiating a full code patch.

### 6. Native Union SFR Format Parser
* **Format Support**: Adds automatic parser detection for plain `typedef union` bitfields with reset value annotations (`_value(0x...)`) grouped inside an IP-level struct with base address offsets, commonly used in newer CXL and PCIe IPs.

### 7. Verbose Classifier Logging
* **Observability**: When `verbose` config is enabled, outputs a detailed report for each job/classifier detailing:
  1. What changed (old vs new definition).
  2. Prompts sent to the LLM.
  3. Raw response received from the LLM.
  4. Affected LLD files and modified line numbers.
