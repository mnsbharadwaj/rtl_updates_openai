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