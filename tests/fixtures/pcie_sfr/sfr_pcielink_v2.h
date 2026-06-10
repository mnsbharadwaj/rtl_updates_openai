/*
 * sfr_pcielink_v2.h  --  PCIELINK SFR header (version 2 / "new")
 *
 * Format: native typedef union bitfield  (uint32, _value(), stNative)
 * IP    : PCIELINK
 *
 * Changes from v1 (with complex descriptions that trigger LLM patching):
 *
 *   CTRL_LT0:
 *     lt_speed    : 2-bit → 3-bit (Gen4 added)          [MULTI_CHANGED/BITWIDTH_CHANGED]
 *     lt_width    : bits 3-5 → 4-6 (shifted)            [OFFSET_CHANGED]
 *     eq_en       : desc cosmetically rephrased          [COMMENT_CHANGED — semantic eq]
 *     retrain_cnt : desc semantically different          [COMMENT_CHANGED — needs LLM]
 *     preset_hint : NEW field                            [FIELD_ADDED]
 *
 *   CTRL_LT1:
 *     det_en      : polarity inverted (active-low now)   [FIELD_POLARITY_CHANGED]
 *     poll_timeout: 8-bit → 9-bit                       [BITWIDTH_CHANGED]
 *
 *   STATUS:  unchanged
 *
 *   ERR_INJECT:
 *     err_type → err_code (renamed)                     [FIELD_RENAMED]
 *     burst_en: NEW field                               [FIELD_ADDED]
 */

#ifndef _SFR_PCIELINK_V2_H_
#define _SFR_PCIELINK_V2_H_

typedef unsigned int uint32;

/* =========================================================================
 *  Register: CTRL_LT0  — Link Training 0 Control Register (v2)
 * ========================================================================= */
typedef union _SFR_PCIELINK_CTRL_LT0_U
{
    uint32 nvalue _value(0x00000000);
    struct
    {
        uint32 lt_en          :  1; // 0-0   [RW]   Link training enable. Software sets this bit to 1 to initiate the PCIe link training state machine. The hardware clears this bit automatically when training completes successfully or when the retrain counter reaches its limit.
        uint32 lt_speed       :  3; // 1-3   [RW]   Target link speed for training negotiation. Encoding: 0=Gen1 (2.5GT/s), 1=Gen2 (5GT/s), 2=Gen3 (8GT/s), 3=Gen4 (16GT/s). Gen4 support requires the remote endpoint to advertise Gen4 capability in the Link Capabilities register. The hardware will downgrade to the highest mutually supported speed if negotiation fails at the requested rate. Note that enabling Gen4 activates the 128b/130b encoding scheme which requires retraining if switching from Gen1/Gen2.
        uint32 lt_width       :  3; // 4-6   [RW]   Target link width expressed as log2 of the number of lanes. Encoding: 0=x1, 1=x2, 2=x4, 3=x8. Used during the link configuration sub-state to negotiate the active lane count.
        uint32 eq_en          :  1; // 7-7   [RW]   Equalization enable during the link training sequence. When asserted, the LTSSM will execute Phase 2 and Phase 3 channel equalization as specified by the PCIe Base Specification Section 4.2.6. This field has no effect for Gen1 or Gen2 operating speeds.
        uint32 retrain_cnt    :  4; // 8-11  [RW]   Automatic retraining threshold. When the physical layer detects an unrecoverable error (symbol lock loss, disparity error, or elastic buffer overflow), the LTSSM automatically enters Recovery state. This field controls how many Recovery.RcvrLock attempts are made before the LTSSM abandons recovery and transitions to Detect, generating a link-down event and setting the train_err flag. Setting this field to 0 disables automatic retraining entirely.
        uint32 preset_hint    :  4; // 12-15 [RW]   Gen3/Gen4 transmitter preset hint for remote endpoint equalization. During Phase 2 equalization the local transmitter advertises this preset index in EQ TS2 ordered sets. Encoding follows PCIe Base Specification Table 4-15. Valid range: 0-10. Values 11-15 are reserved and must not be written.
        uint32 RSVDN0         : 16; // 16-31 [RO]   reserved
    } stNative;
} SFR_PCIELINK_CTRL_LT0, *pSFR_PCIELINK_CTRL_LT0;


/* =========================================================================
 *  Register: CTRL_LT1  — Link Training 1 Control Register (v2)
 * ========================================================================= */
typedef union _SFR_PCIELINK_CTRL_LT1_U
{
    uint32 nvalue _value(0x00000000);
    struct
    {
        uint32 det_en         :  1; // 0-0   [RW]   Receiver detect active-low enable. A value of 0 enables the receiver detect sequence; a value of 1 places the detector in bypass mode. This active-low polarity was introduced to align with the companion PHY interface which uses an inverted detect_req signal. Software must write 0 (not 1) to arm the detection hardware before asserting lt_en.
        uint32 det_timeout    :  8; // 1-8   [RW]   Detect phase timeout in milliseconds. If no receiver is detected within this period, the LTSSM returns to Detect.Quiet and retries. Valid range: 1-255 ms. A value of 0 disables the timeout.
        uint32 poll_en        :  1; // 9-9   [RW]   Polling sub-state enable. When set, the LTSSM will enter Polling.Active after Detect and will transmit TS1 ordered sets on all configured lanes.
        uint32 poll_timeout   :  9; // 10-18 [RW]   Polling phase timeout in milliseconds. If the polling handshake (TS1/TS2 exchange) does not complete within this window, the LTSSM returns to Detect. Extended to 9 bits (512 ms maximum) to support long-cable Gen4 configurations where propagation delay may extend the training window beyond 255 ms. Must be greater than 24 ms per PCIe specification minimum.
        uint32 RSVDN0         : 13; // 19-31 [RO]   reserved
    } stNative;
} SFR_PCIELINK_CTRL_LT1, *pSFR_PCIELINK_CTRL_LT1;


/* =========================================================================
 *  Register: STATUS  — Link Status Register (unchanged in v2)
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
 *  Register: ERR_INJECT  — Error Injection Control Register (v2)
 * ========================================================================= */
typedef union _SFR_PCIELINK_ERR_INJECT_U
{
    uint32 nvalue _value(0x00000000);
    struct
    {
        uint32 err_en         :  1; // 0-0   [RW]   Error injection enable. When set, the error injection engine is armed and will inject errors according to the err_code and err_cnt fields. Clearing this bit immediately disarms the engine; any in-progress injection completes before the engine halts.
        uint32 err_code       :  4; // 1-4   [RW]   Error code selector (renamed from err_type in v1). Specifies which class of PCIe error to inject: 0=LCRC (data link layer CRC), 1=ECRC (end-to-end CRC), 2=SEQ (sequence number), 3=DLLP (data link layer packet), 4=TLP (transaction layer packet header). Values 5-15 are reserved.
        uint32 err_cnt        :  8; // 5-12  [RW]   Number of errors to inject. When non-zero, the engine injects exactly this many errors then auto-clears err_en. When zero, errors are injected continuously until software clears err_en.
        uint32 burst_en       :  1; // 13-13 [RW]   Burst injection mode enable. When set and err_cnt is non-zero, errors are injected in bursts of 4 consecutive TLPs separated by 16 error-free TLPs, creating a realistic bursty error pattern for stress testing link error recovery logic. When clear, each injected error is separated by a configurable inter-error gap determined by the err_cnt scheduling algorithm.
        uint32 RSVDN0         : 18; // 14-31 [RO]   reserved
    } stNative;
} SFR_PCIELINK_ERR_INJECT, *pSFR_PCIELINK_ERR_INJECT;


/* =========================================================================
 *  IP-level register map struct (same base address)
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


#endif /* _SFR_PCIELINK_V2_H_ */
