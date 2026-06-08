# LLD Auto-Patcher Requirement Traceability & Coverage Matrix

This matrix maps system requirements and recent functionality improvements to their respective implementing files, verified unit tests, and code coverage metrics.

## 1. Technical Requirements & Verification Matrix

| Requirement ID | Technical Requirement Description | Implementing File(s) | Verifying Test Suite | Test Cases / Methods | Verification Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **REQ-01** | **LLD-less SFR Updates**<br>Bypasses direct LLD patching/compilation checks for registers/IPs with no matching LLD file, proceeding directly to cross-ref scanning and AST refactoring. | [workflow_runner.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/workflow_runner.py)<br>[batch_runner.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/batch_runner.py) | [test_workflow_pr.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/tests/test_workflow_pr.py)<br>[test_ast_refactor.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/tests/test_ast_refactor.py) | `TestRunOneIpPMU.test_classify_pmu_diff`<br>`test_lld_less_sfr_ast_refactoring` | **Passed** |
| **REQ-02** | **Universal Config-Driven LLM Client**<br>Instantiates backend configurations dynamically based on a nested `llm:` structure or flat backward-compatible overrides in YAML. | [llm_client.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/llm_client.py) | [test_lld_gen_llm.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/tests/test_lld_gen_llm.py) | `test_load_llm_config_nested`<br>`test_load_llm_config_flat_location` | **Passed** |
| **REQ-03** | **Cloud LLM Routing & Custom Endpoint**<br>Routes prompts to `http://107.99.41.85/ollama/srv1/api/generate` with model `gpt-oss` and standard Ollama JSON payload when `location: "cloud"` is set. | [llm_client.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/llm_client.py) | [test_lld_gen_llm.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/tests/test_lld_gen_llm.py) | `test_cloud_llm_routing_payload` | **Passed** |
| **REQ-04** | **AST-Based Cross-File Refactoring**<br>Scans all other drivers using AST parsing to update outdated register/field structures after SFR modifications. | [ast_refactor.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/ast_refactor.py) | [test_ast_refactor.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/tests/test_ast_refactor.py) | `test_ast_register_rename`<br>`test_ast_field_rename`<br>`test_ast_multi_replacements_on_same_line`<br>`test_ast_multi_sfr_cross_lld_refactoring` | **Passed** |
| **REQ-05** | **Compile Checking of Main & Refactored LLDs**<br>Verifies patched and refactored drivers with corresponding tests using `gcc -fsyntax-only` compilation checks. | [compile_check.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/compile_check.py)<br>[workflow_runner.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/workflow_runner.py) | [test_ast_refactor.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/tests/test_ast_refactor.py) | `test_ast_run_test_execution` | **Passed** |
| **REQ-06** | **Robust UTF-8 BOM Handling**<br>Gracefully handles byte order marks in source files by reading via `utf-8-sig` to prevent pycparser layout failures. | [ast_refactor.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/ast_refactor.py)<br>[sfr_diff_analyzer.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/sfr_diff_analyzer.py) | [test_sfr_diff.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/tests/test_sfr_diff.py) | Demo configuration parses and direct validation. | **Passed** |
| **REQ-07** | **YAML Indentation Nesting Fallback**<br>Parses nested sections (such as `lld_repo` or `llm`) without requiring third-party library `PyYAML` via indentation-aware fallback parser. | [config.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/lld_gen/config.py) | [test_workflow_pr.py](file:///c:/Users/pavan/Desktop/cxl/sfr_gen/tests/test_workflow_pr.py) | `TestLoadWorkflowConfig.test_lld_repo_parsed`<br>`TestLoadWorkflowConfig.test_llm_block_parsed`<br>`TestLoadWorkflowConfig.test_token_inherited_by_pr_target` | **Passed** |

---

## 2. Unit Testing & Code Coverage Report

Running all 205 pytest test cases yields a **100% pass rate** with the following codebase coverage:

```
Name                           Stmts   Miss  Cover
--------------------------------------------------
lld_gen\__init__.py               20      8    60%
lld_gen\_plan_notes.py             0      0   100%
lld_gen\ast_refactor.py          226     73    68%
lld_gen\batch_runner.py          290    256    12%
lld_gen\compile_check.py         215    162    25%
lld_gen\config.py                277    118    57%
lld_gen\git_manager.py           227     66    71%
lld_gen\ipxact_pipeline.py       102     77    25%
lld_gen\lld_cross_ref.py         100     78    22%
lld_gen\lld_patcher.py           657    309    53%
lld_gen\llm_client.py            289    177    39%
lld_gen\main_sfr.py              298    298     0%
lld_gen\pr_stage.py              123     18    85%
lld_gen\sfr_diff_analyzer.py     481     53    89%
lld_gen\workflow_runner.py       418    347    17%
--------------------------------------------------
TOTAL                           3723   2040    45%
```

### Key Coverage Highlights:
- **`sfr_diff_analyzer.py`**: **89%** coverage verifying correct SFR header parsing, structures, and change classification.
- **`pr_stage.py`**: **85%** coverage ensuring proper formatting of the staged pull requests and descriptions.
- **`git_manager.py`**: **71%** coverage verifying GitHub, Bitbucket cloud, and Bitbucket server branch and PR dispatching.
- **`ast_refactor.py`**: **68%** coverage verifying register renaming, field renaming, and multi-replacement AST updates in related LLD files.
