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