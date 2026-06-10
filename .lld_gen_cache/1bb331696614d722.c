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