"""
fix_test_names.py -- update test assertions from old IP_REG_FIELD naming to new lld_ip_reg_field naming
"""
import re
from pathlib import Path

VERBS = r'(?:get|set|set1|clear|enable|disable|status)'

for fpath in ['tests/test_sfr_diff.py', 'tests/demo/test_demo_patch.py']:
    text = Path(fpath).read_text(encoding='utf-8')
    
    # Match quoted strings like 'DMA_CTRL_EN_get' or "DMA_CTRL_EN_get"
    pattern = re.compile(
        r"""(['"])([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+_""" + VERBS + r""")(['"])"""
    )
    
    def replacer(m):
        q1, name, q2 = m.group(1), m.group(2), m.group(3)
        return f"{q1}lld_{name.lower()}{q2}"
    
    fixed = pattern.sub(replacer, text)
    
    # Also fix f-string patterns like f"DMA_CTRL_EN_get" used in assert messages
    pattern2 = re.compile(
        r'f"([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+_' + VERBS + r')'
    )
    def replacer2(m):
        name = m.group(1)
        return f'f"lld_{name.lower()}'
    fixed = pattern2.sub(replacer2, fixed)

    if fixed != text:
        Path(fpath).write_text(fixed, encoding='utf-8')
        print(f"[OK] Updated {fpath}")
    else:
        print(f"[--] No changes needed in {fpath}")
