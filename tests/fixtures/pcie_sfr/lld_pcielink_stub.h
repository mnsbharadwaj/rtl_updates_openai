/*
 * lld_pcielink_stub.h  --  Minimal LLD stub for PCIELINK (v1 register layout)
 *
 * Used by diff/patch tests to simulate a real LLD header that the patcher
 * reads and modifies.  Only the functions touched by the v1→v2 changes are
 * included; the rest is scaffolding so the file compiles standalone.
 */

#ifndef _LLD_PCIELINK_STUB_H_
#define _LLD_PCIELINK_STUB_H_

/* Minimal type helpers so this compiles without extra headers */
typedef unsigned char  uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int   uint32_t;
typedef unsigned int   uint32;

/* ── Forward declarations ─────────────────────────────────────────────────── */
typedef struct _SFR_PCIELINK_CTRL_LT0_U SFR_PCIELINK_CTRL_LT0;
typedef struct _SFR_PCIELINK_CTRL_LT1_U SFR_PCIELINK_CTRL_LT1;
typedef struct _SFR_PCIELINK_STATUS_U   SFR_PCIELINK_STATUS;
typedef struct _SFR_PCIELINK_ERR_INJECT_U SFR_PCIELINK_ERR_INJECT;

/* ── SFR union stubs (v1 layout) ─────────────────────────────────────────── */
struct _SFR_PCIELINK_CTRL_LT0_U {
    union {
        uint32 nvalue;
        struct {
            uint32 lt_en       :  1;
            uint32 lt_speed    :  2;   /* v1: 2-bit */
            uint32 lt_width    :  3;
            uint32 eq_en       :  1;
            uint32 _rsvd0      :  1;
            uint32 retrain_cnt :  4;
            uint32 _rsvd1      : 20;
        } stNative;
    };
};

struct _SFR_PCIELINK_CTRL_LT1_U {
    union {
        uint32 nvalue;
        struct {
            uint32 det_en      :  1;
            uint32 det_timeout :  8;
            uint32 poll_en     :  1;
            uint32 poll_timeout:  8;   /* v1: 8-bit */
            uint32 _rsvd0      : 14;
        } stNative;
    };
};

struct _SFR_PCIELINK_STATUS_U {
    union {
        uint32 nvalue;
        struct {
            uint32 link_up    :  1;
            uint32 link_speed :  2;
            uint32 link_width :  3;
            uint32 train_err  :  1;
            uint32 _rsvd0     : 25;
        } stNative;
    };
};

struct _SFR_PCIELINK_ERR_INJECT_U {
    union {
        uint32 nvalue;
        struct {
            uint32 err_en   :  1;
            uint32 err_type :  4;   /* v1: named err_type */
            uint32 err_cnt  :  8;
            uint32 _rsvd0   : 19;
        } stNative;
    };
};

/* ++ LINK_CAP stub (not in v1 hw, added for LLM-patched code that may reference gen4_cap) */
struct _SFR_PCIELINK_LINK_CAP_U {
    union {
        uint32 nvalue;
        struct {
            uint32 gen4_cap : 1;
            uint32 _rsvd0   : 31;
        } stNative;
    };
};
typedef struct _SFR_PCIELINK_LINK_CAP_U SFR_PCIELINK_LINK_CAP;

/* ---- IP-level SFR map -------------------------------------------------------- */
typedef struct {
    SFR_PCIELINK_CTRL_LT0   stCTRL_LT0;
    SFR_PCIELINK_CTRL_LT1   stCTRL_LT1;
    SFR_PCIELINK_STATUS     stSTATUS;
    SFR_PCIELINK_ERR_INJECT stERR_INJECT;
    SFR_PCIELINK_LINK_CAP   stLINK_CAP;   /* stub -- may be referenced by patched code */
} SFR_PCIELINK;

/* ── LLD context struct ───────────────────────────────────────────────────── */
struct lld_pcielink {
    SFR_PCIELINK *pSFR;
};

/* =========================================================================
 *  CTRL_LT0 functions (v1 layout)
 * ========================================================================= */

/** @brief Get link training enable */
static inline uint8_t lld_pcielink_ctrl_lt0_lt_en_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT0.stNative.lt_en);
}

/** @brief Set link training enable */
static inline void lld_pcielink_ctrl_lt0_lt_en_set(struct lld_pcielink *lld, uint8_t val) {
    lld->pSFR->stCTRL_LT0.stNative.lt_en = val;
}

/** @brief Get target link speed (2-bit in v1: 0=Gen1 1=Gen2 2=Gen3) */
static inline uint8_t lld_pcielink_ctrl_lt0_lt_speed_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT0.stNative.lt_speed);
}

/** @brief Set target link speed */
static inline void lld_pcielink_ctrl_lt0_lt_speed_set(struct lld_pcielink *lld, uint8_t val) {
    lld->pSFR->stCTRL_LT0.stNative.lt_speed = val;
}

/** @brief Get equalization enable */
static inline uint8_t lld_pcielink_ctrl_lt0_eq_en_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT0.stNative.eq_en);
}

/** @brief Set equalization enable */
static inline void lld_pcielink_ctrl_lt0_eq_en_set(struct lld_pcielink *lld, uint8_t val) {
    lld->pSFR->stCTRL_LT0.stNative.eq_en = val;
}

/** @brief Get retrain count limit */
static inline uint8_t lld_pcielink_ctrl_lt0_retrain_cnt_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT0.stNative.retrain_cnt);
}

/** @brief Set retrain count limit */
static inline void lld_pcielink_ctrl_lt0_retrain_cnt_set(struct lld_pcielink *lld, uint8_t val) {
    lld->pSFR->stCTRL_LT0.stNative.retrain_cnt = val;
}

/* =========================================================================
 *  CTRL_LT1 functions (v1 layout)
 * ========================================================================= */

/** @brief Get detect enable */
static inline uint8_t lld_pcielink_ctrl_lt1_det_en_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT1.stNative.det_en);
}

/** @brief Set detect enable */
static inline void lld_pcielink_ctrl_lt1_det_en_set(struct lld_pcielink *lld, uint8_t val) {
    lld->pSFR->stCTRL_LT1.stNative.det_en = val;
}

/** @brief Get poll timeout (8-bit in v1) */
static inline uint8_t lld_pcielink_ctrl_lt1_poll_timeout_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT1.stNative.poll_timeout);
}

/** @brief Set poll timeout */
static inline void lld_pcielink_ctrl_lt1_poll_timeout_set(struct lld_pcielink *lld, uint8_t val) {
    lld->pSFR->stCTRL_LT1.stNative.poll_timeout = val;
}

/* =========================================================================
 *  STATUS functions (unchanged v1→v2)
 * ========================================================================= */

/** @brief Get link up status */
static inline uint8_t lld_pcielink_status_link_up_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stSTATUS.stNative.link_up);
}

/** @brief Get training error flag */
static inline uint8_t lld_pcielink_status_train_err_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stSTATUS.stNative.train_err);
}

/** @brief Clear training error flag (W1C) */
static inline void lld_pcielink_status_train_err_clear(struct lld_pcielink *lld) {
    lld->pSFR->stSTATUS.stNative.train_err = 1U; /* W1C */
}

/* =========================================================================
 *  ERR_INJECT functions (v1 layout — field named err_type)
 * ========================================================================= */

/** @brief Get error injection enable */
static inline uint8_t lld_pcielink_err_inject_err_en_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stERR_INJECT.stNative.err_en);
}

/** @brief Set error injection enable */
static inline void lld_pcielink_err_inject_err_en_set(struct lld_pcielink *lld, uint8_t val) {
    lld->pSFR->stERR_INJECT.stNative.err_en = val;
}

/** @brief Get error type (v1 name: err_type) */
static inline uint8_t lld_pcielink_err_inject_err_type_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stERR_INJECT.stNative.err_type);
}

/** @brief Set error type (v1 name: err_type) */
static inline void lld_pcielink_err_inject_err_type_set(struct lld_pcielink *lld, uint8_t val) {
    lld->pSFR->stERR_INJECT.stNative.err_type = val;
}

#endif /* _LLD_PCIELINK_STUB_H_ */
