import sys; sys.path.insert(0, '.')
from lld_gen.sfr_diff_analyzer import classify_sfr_diff

changes = classify_sfr_diff(
    'tests/workspace_demo/old_sfr/sfr_pmu.h',
    'tests/workspace_demo/new_sfr/sfr_pmu.h'
)
print(f"{'Register':<15} {'Field':<12} {'Type':<22} Details")
print('-'*80)
for c in changes:
    fn = c.field_name or '--'
    print(f"{c.reg_name:<15} {fn:<12} {c.change_type:<22} {c.details}")
