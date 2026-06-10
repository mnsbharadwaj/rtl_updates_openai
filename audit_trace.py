import sys; sys.path.insert(0,'.')
from lld_gen.sfr_diff_analyzer import classify_sfr_diff
from lld_gen.semantic_check import apply_semantic_gate, SEMANTIC_CHECK_TYPES
from pathlib import Path
import dataclasses

V1 = Path('tests/fixtures/pcie_sfr/sfr_pcielink_v1.h')
V2 = Path('tests/fixtures/pcie_sfr/sfr_pcielink_v2.h')
changes = classify_sfr_diff(V1, V2, ip='PCIELINK')
apply_semantic_gate(changes, llm_client=None, threshold_low=0.75, threshold_high=0.85)

print('=== ChangeRecord fields ===')
for f in dataclasses.fields(changes[0]):
    print(f'  {f.name}: {getattr(changes[0], f.name)!r}')

print()
print('=== All changes with LLD function traceability ===')
for cr in changes:
    ip_lo  = 'pcielink'
    reg_lo = cr.reg_name.lower()
    fld_lo = (cr.field_name or '').lower()
    fns = []
    if fld_lo:
        acc = (cr.new_field.access if cr.new_field else None) or (cr.old_field.access if cr.old_field else 'RW')
        if acc in ('RO','RW','W1C','W1S','RC','RCW1C'):
            fns.append('lld_%s_%s_%s_get' % (ip_lo, reg_lo, fld_lo))
        if acc in ('RW','WO','W1S'):
            fns.append('lld_%s_%s_%s_set' % (ip_lo, reg_lo, fld_lo))
        if acc == 'W1C':
            fns.append('lld_%s_%s_%s_clear' % (ip_lo, reg_lo, fld_lo))
    needs = 'LLM' if cr.needs_llm else ('AUTO' if cr.change_type != 'UNCHANGED' else 'SKIP')
    reg_field = '%s.%s' % (cr.reg_name, cr.field_name or '(REG)')
    print('  [%-22s] %-30s needs=%s' % (cr.change_type, reg_field, needs))
    for fn in fns:
        print('    fn: %s' % fn)

# 2. Check what's in the LLM prompt
print()
print('=== Prompt content check ===')
src = open('lld_gen/llm_client.py', encoding='utf-8').read()
idx = src.find('_LLD_PATCH_USER_TMPL')
print(src[idx:idx+800])
