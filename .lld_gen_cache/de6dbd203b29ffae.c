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