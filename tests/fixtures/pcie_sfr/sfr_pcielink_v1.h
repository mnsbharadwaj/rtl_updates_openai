/*
 * sfr_pcielink_v1.h  --  PCIELINK SFR header (version 1 / "old")
 *
 * Format: native typedef union bitfield  (uint32, _value(), stNative)
 * IP    : PCIELINK
 */

#ifndef _SFR_PCIELINK_V1_H_
#define _SFR_PCIELINK_V1_H_

typedef unsigned int uint32;

/* =========================================================================
 *  Register: CTRL_LT0  — Link Training 0 Control Register
 * ========================================================================= */
typedef union _SFR_PCIELINK_CTRL_LT0_U
{
    uint32 nvalue _value(0x00000000);
    struct
    {
        uint32 lt_en          :  1; // 0-0   [RW]   Link training enable. Software sets this bit to 1 to initiate the PCIe link training state machine. The hardware clears this bit automatically when training completes successfully or when the retrain counter reaches its limit.
        uint32 lt_speed       :  2; // 1-2   [RW]   Target link speed for the training negotiation. Encoding: 0=Gen1 (2.5GT/s), 1=Gen2 (5GT/s), 2=Gen3 (8GT/s). The actual negotiated speed may be lower if the remote endpoint does not support the requested speed.
        uint32 lt_width       :  3; // 3-5   [RW]   Target link width expressed as log2 of the number of lanes. Encoding: 0=x1, 1=x2, 2=x4, 3=x8. Used during the link configuration sub-state to negotiate the active lane count.
        uint32 eq_en          :  1; // 6-6   [RW]   Equalization enable during training. When set, the link training sequence will perform Phase 2 and Phase 3 equalization as defined in the PCIe 3.0 Base Specification Section 4.2.6. Ignored for Gen1 and Gen2 speeds.
        uint32 RSVDN0         :  1; // 7-7   [RO]   reserved
        uint32 retrain_cnt    :  4; // 8-11  [RW]   Maximum number of consecutive retraining attempts the hardware will perform before asserting the link error interrupt. Each failed attempt increments an internal counter. When this counter equals retrain_cnt the LTSSM transitions to Detect and the train_err status bit is set.
        uint32 RSVDN1         : 20; // 12-31 [RO]   reserved
    } stNative;
} SFR_PCIELINK_CTRL_LT0, *pSFR_PCIELINK_CTRL_LT0;


/* =========================================================================
 *  Register: CTRL_LT1  — Link Training 1 Control Register
 * ========================================================================= */
typedef union _SFR_PCIELINK_CTRL_LT1_U
{
    uint32 nvalue _value(0x00000001);
    struct
    {
        uint32 det_en         :  1; // 0-0   [RW]   Receiver detect enable. Software must set this bit to 1 before the LTSSM enters the Detect sub-state. The hardware drives an electrical idle condition on TX and then measures the DC coupling to determine whether a far-end receiver is present on each lane.
        uint32 det_timeout    :  8; // 1-8   [RW]   Detect phase timeout in milliseconds. If no receiver is detected within this period, the LTSSM returns to Detect.Quiet and retries. Valid range: 1-255 ms. A value of 0 disables the timeout.
        uint32 poll_en        :  1; // 9-9   [RW]   Polling sub-state enable. When set, the LTSSM will enter Polling.Active after Detect and will transmit TS1 ordered sets on all configured lanes.
        uint32 poll_timeout   :  8; // 10-17 [RW]   Polling phase timeout in milliseconds. If the polling handshake (TS1/TS2 exchange) does not complete within this window, the LTSSM returns to Detect. Valid range: 1-255 ms. Must be greater than 24 ms per PCIe specification minimum.
        uint32 RSVDN0         : 14; // 18-31 [RO]   reserved
    } stNative;
} SFR_PCIELINK_CTRL_LT1, *pSFR_PCIELINK_CTRL_LT1;


/* =========================================================================
 *  Register: STATUS  — Link Status Register
 * ========================================================================= */
typedef union _SFR_PCIELINK_STATUS_U
{
    uint32 nvalue _value(0x00000000);
    struct
    {
        uint32 link_up        :  1; // 0-0   [RO]   Link up indicator. Set to 1 by hardware when the LTSSM reaches the L0 state and the logical sublayer reports DL_Up. Cleared when the link goes down or enters a low-power state.
        uint32 link_speed     :  2; // 1-2   [RO]   Current negotiated link speed. Encoding matches lt_speed field: 0=Gen1, 1=Gen2, 2=Gen3. Valid only when link_up=1.
        uint32 link_width     :  3; // 3-5   [RO]   Current negotiated link width (log2 of active lanes). Valid only when link_up=1.
        uint32 train_err      :  1; // 6-6   [W1C]  Link training error indicator. Set by hardware when the retrain_cnt limit is exceeded. Software must write 1 to clear this bit. If this bit is set and lt_en is asserted again, training restarts from Detect.
        uint32 RSVDN0         : 25; // 7-31  [RO]   reserved
    } stNative;
} SFR_PCIELINK_STATUS, *pSFR_PCIELINK_STATUS;


/* =========================================================================
 *  Register: ERR_INJECT  — Error Injection Control Register
 * ========================================================================= */
typedef union _SFR_PCIELINK_ERR_INJECT_U
{
    uint32 nvalue _value(0x00000000);
    struct
    {
        uint32 err_en         :  1; // 0-0   [RW]   Error injection enable. When set, the error injection engine is armed and will inject errors according to the err_type and err_cnt fields. Clearing this bit immediately disarms the engine; any in-progress injection completes before the engine halts.
        uint32 err_type       :  4; // 1-4   [RW]   Error type selector. Specifies which class of PCIe error to inject: 0=LCRC (data link layer CRC), 1=ECRC (end-to-end CRC), 2=SEQ (sequence number), 3=DLLP (data link layer packet), 4=TLP (transaction layer packet header). Values 5-15 are reserved.
        uint32 err_cnt        :  8; // 5-12  [RW]   Number of errors to inject. When non-zero, the engine injects exactly this many errors then auto-clears err_en. When zero, errors are injected continuously until software clears err_en.
        uint32 RSVDN0         : 19; // 13-31 [RO]   reserved
    } stNative;
} SFR_PCIELINK_ERR_INJECT, *pSFR_PCIELINK_ERR_INJECT;


/* =========================================================================
 *  IP-level register map struct
 *  Base address: 0x10000000
 * ========================================================================= */
// Base address: 0x10000000
typedef struct _SFR_PCIELINK_WRP_RW_S
{
    SFR_PCIELINK_CTRL_LT0   stCTRL_LT0;
    SFR_PCIELINK_CTRL_LT1   stCTRL_LT1;
    SFR_PCIELINK_STATUS     stSTATUS;
    SFR_PCIELINK_ERR_INJECT stERR_INJECT;
} SFR_PCIELINK_WRP_RW, *pSFR_PCIELINK_WRP_RW;


#endif /* _SFR_PCIELINK_V1_H_ */
