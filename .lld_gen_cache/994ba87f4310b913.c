static inline uint8_t lld_pcielink_ctrl_lt1_poll_timeout_get(struct lld_pcielink *lld) {
    return (uint8_t)(lld->pSFR->stCTRL_LT1.stNative.poll_timeout);
}
/** @brief Set poll timeout */

static inline void lld_pcielink_ctrl_lt1_poll_timeout_set(struct lld_pcielink *lld, uint8_t val) {
    /* Ensure polling is disabled before setting the timeout */
    if (lld->pSFR->stCTRL_LT1.stNative.poll_en == 0) {
        lld->pSFR->stCTRL_LT1.stNative.poll_timeout = val;
    } else {
        /* Clamp value to minimum of 4 and ensure it's within valid range */
        uint8_t clamped_val = (val < 4) ? 4 : val;
        if (clamped_val > 511) {
            clamped_val = 511;
        }
        lld->pSFR->stCTRL_LT1.stNative.poll_timeout = clamped_val;
    }
}