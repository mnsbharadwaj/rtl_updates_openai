# IP Change Traceability Report: `PCIELINK`

> Generated: 2026-06-10 18:41 UTC  
> SFR v1: `sfr_pcielink_v1.h`  
> SFR v2: `sfr_pcielink_v2.h`

## Summary

| Metric | Value |
|--------|-------|
| Total field changes detected | **10** |
| Unchanged (skipped) | 0 |
| Auto-patched (no LLM needed) | 3 |
| LLM-patched (semantic change) | 7 |
| Registers affected | 3 |

### Registers affected

- `CTRL_LT0` — 5 change(s)
- `CTRL_LT1` — 2 change(s)
- `ERR_INJECT` — 3 change(s)

### Change type key

| Badge | Meaning |
|-------|---------|
| ![AUTO](https://img.shields.io/badge/patch-AUTO-blue) | Deterministic rename/delete/access patch — no LLM needed |
| ![LLM](https://img.shields.io/badge/patch-LLM-purple) | Semantic description change — LLM generates patched function |
| ![SKIP](https://img.shields.io/badge/patch-SKIP-lightgrey) | Field unchanged between v1 and v2 |

---

## Field-by-Field Change Trace

### Register `CTRL_LT0`

#### `CTRL_LT0.eq_en`  ![MULTI](https://img.shields.io/badge/MULTI-purple)  ![LLM](https://img.shields.io/badge/patch-LLM-purple)

**Bit-level diff:**

| Property | v1 | v2 |
|----------|----|----|
| Bit position | `[6:6]` | `[7:7]` |

**Description change:**

| | Description |
|--|-------------|
| v1 | Equalization enable during training. When set, the link training sequence will perform Phase 2 and Phase 3 equalization as defined in the PCIe 3.0 Base Specification Section 4.2.6. Ignored for Gen1 an… |
| v2 | Equalization enable during the link training sequence. When asserted, the LTSSM will execute Phase 2 and Phase 3 channel equalization as specified by the PCIe Base Specification Section 4.2.6. This fi… |

**LLD functions affected:**

```
Bitfield access: lld->pSFR->stCTRL_LT0.stNative.eq_en
```

| Function | Action |
|----------|--------|
| `lld_pcielink_ctrl_lt0_eq_en_get()` | Read field value → return |
| `lld_pcielink_ctrl_lt0_eq_en_set()` | Write field value ← val |

**Patched LLD (LLM):**

```c
static inline uint8_t lld_pcielink_ctrl_lt0_eq_en_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT0.stNative.eq_en);
}

/** @brief Set equalization enable */

static inline void lld_pcielink_ctrl_lt0_eq_en_set(struct lld_pcielink *lld, uint8_t val) {
    /* 0 = disable retraining; valid: 0-15 */
    if (val > 15) {
        val = 15; /* Clamp to max value */
    }
    lld->pSFR->stCTRL_LT0.stNative.eq_en = val;
}
```

---

#### `CTRL_LT0.lt_speed`  ![MULTI](https://img.shields.io/badge/MULTI-purple)  ![LLM](https://img.shields.io/badge/patch-LLM-purple)

**Bit-level diff:**

| Property | v1 | v2 |
|----------|----|----|
| Bit position | `[2:1]` | `[3:1]` |
| Width | `2-bit` | `3-bit` |

**Description change:**

| | Description |
|--|-------------|
| v1 | Target link speed for the training negotiation. Encoding: 0=Gen1 (2.5GT/s), 1=Gen2 (5GT/s), 2=Gen3 (8GT/s). The actual negotiated speed may be lower if the remote endpoint does not support the request… |
| v2 | Target link speed for training negotiation. Encoding: 0=Gen1 (2.5GT/s), 1=Gen2 (5GT/s), 2=Gen3 (8GT/s), 3=Gen4 (16GT/s). Gen4 support requires the remote endpoint to advertise Gen4 capability in the L… |

**LLD functions affected:**

```
Bitfield access: lld->pSFR->stCTRL_LT0.stNative.lt_speed
```

| Function | Action |
|----------|--------|
| `lld_pcielink_ctrl_lt0_lt_speed_get()` | Read field value → return |
| `lld_pcielink_ctrl_lt0_lt_speed_set()` | Write field value ← val |

**Patched LLD (LLM):**

```c
static inline uint8_t lld_pcielink_ctrl_lt0_lt_speed_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT0.stNative.lt_speed);
}

/** @brief Set target link speed
 *
 * @param val New value for the target link speed.
 *            Valid values: 0-3 (Gen1-Gen4).
 *            Special value: 3 = Gen4 support requires remote endpoint to advertise Gen4 capability in Link Capabilities register.
 */

static inline void lld_pcielink_ctrl_lt0_lt_speed_set(struct lld_pcielink *lld, uint8_t val) {
    if (val > 3) {
        /* Special case for Gen4 support */
        lld->pSFR->stCTRL_LT0.stNative.lt_speed = 3; /* Enable Gen4 */
        /* Note: Re-training is required if switching from Gen1/Gen2 to Gen4. */
    } else {
        lld->pSFR->stCTRL_LT0.stNative.lt_speed = val;
    }
}
```

---

#### `CTRL_LT0.lt_width`  ![OFFSET](https://img.shields.io/badge/OFFSET-orange)  ![AUTO](https://img.shields.io/badge/patch-AUTO-blue)

**Bit-level diff:**

| Property | v1 | v2 |
|----------|----|----|
| Bit position | `[5:3]` | `[6:4]` |

**Description change:**

| | Description |
|--|-------------|
| v1 | Target link width expressed as log2 of the number of lanes. Encoding: 0=x1, 1=x2, 2=x4, 3=x8. Used during the link configuration sub-state to negotiate the active lane count. |
| v2 | Target link width expressed as log2 of the number of lanes. Encoding: 0=x1, 1=x2, 2=x4, 3=x8. Used during the link configuration sub-state to negotiate the active lane count. |

**LLD functions affected:**

```
Bitfield access: lld->pSFR->stCTRL_LT0.stNative.lt_width
```

| Function | Action |
|----------|--------|
| `lld_pcielink_ctrl_lt0_lt_width_get()` | Read field value → return |
| `lld_pcielink_ctrl_lt0_lt_width_set()` | Write field value ← val |

---

#### `CTRL_LT0.preset_hint`  ![ADDED](https://img.shields.io/badge/ADDED-green)  ![LLM](https://img.shields.io/badge/patch-LLM-purple)

**Bit-level diff:**

| Property | v1 | v2 |
|----------|----|----|
| Status | `—` | `**NEW field**` |

**Description change:**

| | Description |
|--|-------------|
| v1 |  |
| v2 | Gen3/Gen4 transmitter preset hint for remote endpoint equalization. During Phase 2 equalization the local transmitter advertises this preset index in EQ TS2 ordered sets. Encoding follows PCIe Base Sp… |

**LLD functions affected:**

```
Bitfield access: lld->pSFR->stCTRL_LT0.stNative.preset_hint
```

| Function | Action |
|----------|--------|
| `lld_pcielink_ctrl_lt0_preset_hint_get()` | Read field value → return |
| `lld_pcielink_ctrl_lt0_preset_hint_set()` | Write field value ← val |

**Patched LLD (LLM):**

```c
static inline uint8_t lld_ip_ctrl_lt0_preset_hint_get(struct lld_ip_lo *lld)
{
    return (uint8_t)(lld->pSFR->stCTRL_LT0.stNative.preset_hint);
}

static inline void lld_ip_ctrl_lt0_preset_hint_set(struct lld_ip_lo *lld, uint8_t val)
{
    lld->pSFR->stCTRL_LT0.stNative.preset_hint = val;
}
```

**Notes:**

- New function generated by LLM (FIELD_ADDED)

---

#### `CTRL_LT0.retrain_cnt`  ![COMMENT](https://img.shields.io/badge/COMMENT-blue)  ![LLM](https://img.shields.io/badge/patch-LLM-purple)

**Bit-level diff:**

*(bit position, width, access, reset unchanged)*

**Description change:**

| | Description |
|--|-------------|
| v1 | Maximum number of consecutive retraining attempts the hardware will perform before asserting the link error interrupt. Each failed attempt increments an internal counter. When this counter equals retr… |
| v2 | Automatic retraining threshold. When the physical layer detects an unrecoverable error (symbol lock loss, disparity error, or elastic buffer overflow), the LTSSM automatically enters Recovery state. T… |

**LLD functions affected:**

```
Bitfield access: lld->pSFR->stCTRL_LT0.stNative.retrain_cnt
```

| Function | Action |
|----------|--------|
| `lld_pcielink_ctrl_lt0_retrain_cnt_get()` | Read field value → return |
| `lld_pcielink_ctrl_lt0_retrain_cnt_set()` | Write field value ← val |

**Patched LLD (LLM):**

```c
static inline uint8_t lld_pcielink_ctrl_lt0_retrain_cnt_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT0.stNative.retrain_cnt);
}

/**
 * @brief Set retraining count limit
 *
 * @param lld Pointer to the LLD structure
 * @param val New retraining count limit
 */

static inline void lld_pcielink_ctrl_lt0_retrain_cnt_set(struct lld_pcielink *lld, uint8_t val) {
    /* Special value: 0 = disable retraining */
    if (val == 0) {
        lld->pSFR->stCTRL_LT0.stNative.retrain_cnt = 0;
        /* Disable retraining by setting the field to 0 */
    } else {
        /* Valid range: 0-15 */
        if (val > 15) val = 15;
        lld->pSFR->stCTRL_LT0.stNative.retrain_cnt = val;
        /* Set the new retraining count limit */
    }
}
```

---

### Register `CTRL_LT1`

#### `CTRL_LT1.det_en`  ![MULTI](https://img.shields.io/badge/MULTI-purple)  ![LLM](https://img.shields.io/badge/patch-LLM-purple)

**Bit-level diff:**

| Property | v1 | v2 |
|----------|----|----|
| Reset value | `0x1` | `0x0` |

**Description change:**

| | Description |
|--|-------------|
| v1 | Receiver detect enable. Software must set this bit to 1 before the LTSSM enters the Detect sub-state. The hardware drives an electrical idle condition on TX and then measures the DC coupling to determ… |
| v2 | Receiver detect active-low enable. A value of 0 enables the receiver detect sequence; a value of 1 places the detector in bypass mode. This active-low polarity was introduced to align with the compani… |

**LLD functions affected:**

```
Bitfield access: lld->pSFR->stCTRL_LT1.stNative.det_en
```

| Function | Action |
|----------|--------|
| `lld_pcielink_ctrl_lt1_det_en_get()` | Read field value → return |
| `lld_pcielink_ctrl_lt1_det_en_set()` | Write field value ← val |

**Patched LLD (LLM):**

```c
static inline uint8_t lld_pcielink_ctrl_lt1_det_en_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT1.stNative.det_en);
}

/** @brief Set receiver detect active-low enable */

static inline void lld_pcielink_ctrl_lt1_det_en_set(struct lld_pcielink *lld, uint8_t val) {
    /* 0 = disable retraining; valid: 0-15 */
    if (val == 0) {
        lld->pSFR->stCTRL_LT1.stNative.det_en = 0;
    } else {
        lld->pSFR->stCTRL_LT1.stNative.det_en = 1;
    }
}
```

---

#### `CTRL_LT1.poll_timeout`  ![MULTI](https://img.shields.io/badge/MULTI-purple)  ![LLM](https://img.shields.io/badge/patch-LLM-purple)

**Bit-level diff:**

| Property | v1 | v2 |
|----------|----|----|
| Bit position | `[17:10]` | `[18:10]` |
| Width | `8-bit` | `9-bit` |

**Description change:**

| | Description |
|--|-------------|
| v1 | Polling phase timeout in milliseconds. If the polling handshake (TS1/TS2 exchange) does not complete within this window, the LTSSM returns to Detect. Valid range: 1-255 ms. Must be greater than 24 ms … |
| v2 | Polling phase timeout in milliseconds. If the polling handshake (TS1/TS2 exchange) does not complete within this window, the LTSSM returns to Detect. Extended to 9 bits (512 ms maximum) to support lon… |

**LLD functions affected:**

```
Bitfield access: lld->pSFR->stCTRL_LT1.stNative.poll_timeout
```

| Function | Action |
|----------|--------|
| `lld_pcielink_ctrl_lt1_poll_timeout_get()` | Read field value → return |
| `lld_pcielink_ctrl_lt1_poll_timeout_set()` | Write field value ← val |

**Patched LLD (LLM):**

```c
static inline uint8_t lld_pcielink_ctrl_lt1_poll_timeout_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT1.stNative.poll_timeout);
}

/**
 * @brief Set poll timeout in milliseconds
 *
 * @param lld Pointer to the LLD instance
 * @param val Polling phase timeout in milliseconds
 */

static inline void lld_pcielink_ctrl_lt1_poll_timeout_set(struct lld_pcielink *lld, uint8_t val) {
    /* Ensure the value is within the new valid range of 0-512 ms */
    if (val > 512) {
        val = 512; /* Clamp to maximum allowed value */
    }
    lld->pSFR->stCTRL_LT1.stNative.poll_timeout = val;
}
```

---

### Register `ERR_INJECT`

#### `ERR_INJECT.burst_en`  ![ADDED](https://img.shields.io/badge/ADDED-green)  ![LLM](https://img.shields.io/badge/patch-LLM-purple)

**Bit-level diff:**

| Property | v1 | v2 |
|----------|----|----|
| Status | `—` | `**NEW field**` |

**Description change:**

| | Description |
|--|-------------|
| v1 |  |
| v2 | Burst injection mode enable. When set and err_cnt is non-zero, errors are injected in bursts of 4 consecutive TLPs separated by 16 error-free TLPs, creating a realistic bursty error pattern for stress… |

**LLD functions affected:**

```
Bitfield access: lld->pSFR->stERR_INJECT.stNative.burst_en
```

| Function | Action |
|----------|--------|
| `lld_pcielink_err_inject_burst_en_get()` | Read field value → return |
| `lld_pcielink_err_inject_burst_en_set()` | Write field value ← val |

**Patched LLD (LLM):**

```c
static inline uint8_t lld_pcie_link_err_inject_burst_en_get(struct lld_pcielink *lld)
{
    return (uint8_t)(lld->pSFR->stERR_INJECT.stNative.burst_en);
}

static inline void lld_pcie_link_err_inject_burst_en_set(struct lld_pcielink *lld, uint8_t val)
{
    lld->pSFR->stERR_INJECT.stNative.burst_en = val;
}
```

**Notes:**

- New function generated by LLM (FIELD_ADDED)

---

#### `ERR_INJECT.err_en`  ![COMMENT](https://img.shields.io/badge/COMMENT-blue)  ![AUTO](https://img.shields.io/badge/patch-AUTO-blue)

**Bit-level diff:**

*(bit position, width, access, reset unchanged)*

**Description change:**

| | Description |
|--|-------------|
| v1 | Error injection enable. When set, the error injection engine is armed and will inject errors according to the err_type and err_cnt fields. Clearing this bit immediately disarms the engine; any in-prog… |
| v2 | Error injection enable. When set, the error injection engine is armed and will inject errors according to the err_code and err_cnt fields. Clearing this bit immediately disarms the engine; any in-prog… |

**LLD functions affected:**

```
Bitfield access: lld->pSFR->stERR_INJECT.stNative.err_en
```

| Function | Action |
|----------|--------|
| `lld_pcielink_err_inject_err_en_get()` | Read field value → return |
| `lld_pcielink_err_inject_err_en_set()` | Write field value ← val |

**Patched LLD (AUTO):**

```c
static inline uint8_t lld_pcielink_err_inject_err_en_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stERR_INJECT.stNative.err_en);
}

/** @brief Set error injection enable */

static inline void lld_pcielink_err_inject_err_en_set(struct lld_pcielink *lld, uint8_t val) {
    lld->pSFR->stERR_INJECT.stNative.err_en = val;
}

/** @brief Get error type (v1 name: err_type) */
```

**Notes:**

- AUTO patch (deterministic — no LLM needed)

---

#### `ERR_INJECT.err_type`  ![RENAMED](https://img.shields.io/badge/RENAMED-yellow)  ![AUTO](https://img.shields.io/badge/patch-AUTO-blue)

**Bit-level diff:**

*(bit position, width, access, reset unchanged)*

**Description change:**

| | Description |
|--|-------------|
| v1 | Error type selector. Specifies which class of PCIe error to inject: 0=LCRC (data link layer CRC), 1=ECRC (end-to-end CRC), 2=SEQ (sequence number), 3=DLLP (data link layer packet), 4=TLP (transaction … |
| v2 | Error code selector (renamed from err_type in v1). Specifies which class of PCIe error to inject: 0=LCRC (data link layer CRC), 1=ECRC (end-to-end CRC), 2=SEQ (sequence number), 3=DLLP (data link laye… |

**LLD functions affected:**

```
Bitfield access: lld->pSFR->stERR_INJECT.stNative.err_type
```

| Function | Action |
|----------|--------|
| `lld_pcielink_err_inject_err_type_get()` | Read field value → return |
| `lld_pcielink_err_inject_err_type_set()` | Write field value ← val |

**Patched LLD (AUTO):**

```c
static inline uint8_t lld_pcielink_err_inject_err_type_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stERR_INJECT.stNative.err_type);
}

/** @brief Set error type (v1 name: err_type) */

static inline void lld_pcielink_err_inject_err_type_set(struct lld_pcielink *lld, uint8_t val) {
    lld->pSFR->stERR_INJECT.stNative.err_type = val;
}

#endif /* _LLD_PCIELINK_STUB_H_ */
```

**Notes:**

- AUTO patch (deterministic — no LLM needed)

---

## Semantic Gate Results

The semantic equivalence gate classifies each COMMENT_CHANGED / MULTI_CHANGED
description change before deciding whether to invoke the LLM:

| Field | Gate result | Method | Details |
|-------|-------------|--------|---------|
| `CTRL_LT0.eq_en` | CHANGED (send to LLM) | `heuristic_low)` |  |
| `CTRL_LT0.lt_speed` | CHANGED (send to LLM) | `heuristic_low)` |  |
| `CTRL_LT0.retrain_cnt` | CHANGED (send to LLM) | `heuristic_low)` |  |
| `CTRL_LT1.det_en` | CHANGED (send to LLM) | `heuristic_low)` |  |
| `CTRL_LT1.poll_timeout` | CHANGED (send to LLM) | `heuristic_low)` |  |
| `ERR_INJECT.err_en` | EQUIVALENT (skip LLM) | `heuristic_high)` |  |

---

## Acceptance Criteria Status

| Criterion | Status |
|-----------|--------|
| Every field change traced to LLD function(s) | ✅ Deterministic via `lld_{ip}_{reg}_{field}_{verb}` naming |
| Old+new description sent to LLM with bitfield context | ✅ `build_patch_context()` assembles full IR summary + C access path |
| IP-level .md summary generated | ✅ This document |

*Generated by `lld_gen.change_summary.generate_ip_change_summary()`*