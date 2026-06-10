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