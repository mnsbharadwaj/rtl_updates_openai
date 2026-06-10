"""
semantic_check.py — Two-stage semantic equivalence gate for description changes.

When a SFR field's description text changes (COMMENT_CHANGED, MULTI_CHANGED,
DESCRIPTION_ADDED, FIELD_POLARITY_CHANGED), this module decides whether the
change is semantically meaningful (different intent) or merely cosmetic
(same intent, different wording).

Decision matrix:
    similarity > threshold_high  →  auto-skip: mark semantic_equivalent=True
    similarity < threshold_low   →  auto-patch: skip LLM check, go straight to patching
    threshold_low ≤ similarity ≤ threshold_high  →  LLM decides (YES/NO prompt)

Change types that trigger this gate:
    COMMENT_CHANGED, MULTI_CHANGED, DESCRIPTION_ADDED, FIELD_POLARITY_CHANGED
"""
from __future__ import annotations

import difflib
import logging
from typing import Optional, Tuple

from lld_gen.sfr_diff_analyzer import ChangeRecord, ChangeType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Change types that go through the semantic equivalence gate
# ---------------------------------------------------------------------------
SEMANTIC_CHECK_TYPES: frozenset[str] = frozenset({
    ChangeType.COMMENT_CHANGED,
    ChangeType.MULTI_CHANGED,
    ChangeType.DESCRIPTION_ADDED,
    ChangeType.FIELD_POLARITY_CHANGED,
})

# ---------------------------------------------------------------------------
# LLM prompt for the semantic equivalence check  (lightweight, yes/no)
# ---------------------------------------------------------------------------
_SEMANTIC_CHECK_SYSTEM = (
    "You are a hardware documentation analyst reviewing SFR register field descriptions. "
    "Determine whether two descriptions convey the same hardware behaviour. "
    "Answer ONLY with YES (same meaning, possibly different wording) or NO (different meaning)."
)

_SEMANTIC_CHECK_USER = """\
Register: {reg_name}
Field: {field_name}
Old description: {old_desc}
New description: {new_desc}

Are these two descriptions semantically equivalent (same hardware behaviour, possibly rephrased)?
Answer YES or NO only:"""


# ---------------------------------------------------------------------------
# Core check function
# ---------------------------------------------------------------------------
def check_semantic_equivalence(
    old_desc:        str,
    new_desc:        str,
    reg_name:        str,
    field_name:      str = "",
    llm_client:      Optional[object] = None,
    threshold_low:   float = 0.75,
    threshold_high:  float = 0.85,
) -> Tuple[bool, str]:
    """
    Determine whether old_desc and new_desc are semantically equivalent.

    Returns:
        (is_equivalent, method)  where method is one of:
            'heuristic_high'  — similarity > threshold_high  (auto-skip)
            'heuristic_low'   — similarity < threshold_low   (auto-patch)
            'llm_yes'         — LLM said YES  (skip)
            'llm_no'          — LLM said NO   (patch)
            'llm_unavailable' — LLM not reachable, treated as not equivalent
    """
    old_stripped = old_desc.strip().lower()
    new_stripped = new_desc.strip().lower()

    # Edge cases
    if old_stripped == new_stripped:
        return True, "heuristic_exact"

    if not old_stripped or not new_stripped:
        # One is empty — treat as different (DESCRIPTION_ADDED or removed)
        return False, "heuristic_empty"

    # Compute string similarity
    ratio = difflib.SequenceMatcher(None, old_stripped, new_stripped).ratio()

    logger.debug(
        "[SEMANTIC] %s.%s  ratio=%.3f  low=%.2f  high=%.2f",
        reg_name, field_name, ratio, threshold_low, threshold_high,
    )

    if ratio > threshold_high:
        logger.info(
            "[SEMANTIC] %s.%s  → AUTO-SKIP (ratio=%.3f > %.2f) — cosmetic wording change",
            reg_name, field_name, ratio, threshold_high,
        )
        return True, "heuristic_high"

    if ratio < threshold_low:
        logger.info(
            "[SEMANTIC] %s.%s  → AUTO-PATCH (ratio=%.3f < %.2f) — clear semantic change",
            reg_name, field_name, ratio, threshold_low,
        )
        return False, "heuristic_low"

    # Borderline zone: call LLM
    if llm_client is None or not getattr(llm_client, "available", False):
        logger.warning(
            "[SEMANTIC] %s.%s  borderline (ratio=%.3f) but LLM unavailable — treating as different",
            reg_name, field_name, ratio,
        )
        return False, "llm_unavailable"

    try:
        user_prompt = _SEMANTIC_CHECK_USER.format(
            reg_name=reg_name,
            field_name=field_name or reg_name,
            old_desc=old_desc.strip(),
            new_desc=new_desc.strip(),
        )
        raw = llm_client._call(  # type: ignore[attr-defined]
            system=_SEMANTIC_CHECK_SYSTEM,
            user=user_prompt,
            max_tokens=8,
        ).strip().upper()

        is_equiv = raw.startswith("YES")
        method   = "llm_yes" if is_equiv else "llm_no"
        logger.info(
            "[SEMANTIC] %s.%s  LLM says %s (ratio=%.3f)",
            reg_name, field_name, "YES→skip" if is_equiv else "NO→patch", ratio,
        )
        return is_equiv, method

    except Exception as exc:
        logger.warning(
            "[SEMANTIC] %s.%s  LLM call failed (%s) — treating as different",
            reg_name, field_name, exc,
        )
        return False, "llm_unavailable"


# ---------------------------------------------------------------------------
# Apply gate to a list of ChangeRecords (in-place mutation)
# ---------------------------------------------------------------------------
def apply_semantic_gate(
    changes:         list[ChangeRecord],
    llm_client:      Optional[object] = None,
    threshold_low:   float = 0.75,
    threshold_high:  float = 0.85,
) -> None:
    """
    Walk *changes* in-place.  For each record whose change_type is in
    SEMANTIC_CHECK_TYPES and needs_llm=True, run the semantic equivalence
    check.  If equivalent:
        - Set semantic_equivalent = True
        - Set needs_llm = False
        - Downgrade change_type to COMMENT_CHANGED (audit trail)
    """
    for cr in changes:
        if cr.change_type not in SEMANTIC_CHECK_TYPES:
            continue
        if not cr.needs_llm:
            continue

        old_desc = (cr.old_field.desc if cr.old_field else "") or ""
        new_desc = (cr.new_field.desc if cr.new_field else "") or ""

        is_equiv, method = check_semantic_equivalence(
            old_desc=old_desc,
            new_desc=new_desc,
            reg_name=cr.reg_name,
            field_name=cr.field_name or "",
            llm_client=llm_client,
            threshold_low=threshold_low,
            threshold_high=threshold_high,
        )

        if is_equiv:
            cr.semantic_equivalent = True
            cr.needs_llm            = False
            cr.change_type          = ChangeType.COMMENT_CHANGED
            cr.details.append(
                f"semantic_equivalent=True (method={method}) — "
                f"description change is cosmetic; LLM patch skipped"
            )
            logger.info(
                "[SEMANTIC-GATE] %s.%s  → SKIPPED (equivalent, method=%s)",
                cr.reg_name, cr.field_name or "", method,
            )
        else:
            cr.details.append(
                f"semantic_equivalent=False (method={method}) — "
                f"description change is meaningful; LLM patch required"
            )
