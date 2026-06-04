"""Debug: check STATUS block after patch and fix FIELD_ADDED routing."""
import shutil, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lld_gen.sfr_diff_analyzer import classify_sfr_diff, SfrParser
from lld_gen.lld_patcher import LLDPatcher

DEMO = Path('tests/demo')
changes = classify_sfr_diff(DEMO/'sfr_old.h', DEMO/'sfr_new.h', ip='DMA')
new_ir  = SfrParser(ip='DMA').parse_file(DEMO/'sfr_new.h')

td = Path(tempfile.mkdtemp())
lld = td / 'lld.h'
shutil.copy2(DEMO/'lld.h', lld)

p = LLDPatcher(ip='DMA', no_llm=True)
content = p.patch(lld, changes, new_ir=new_ir)

# Save content to inspect
out = Path('debug_patched.h')
out.write_text(content, encoding='utf-8', errors='replace')
print("Written to debug_patched.h")

# Check for BUSY
if 'lld_dma_status_busy_get' in content:
    print("OK: lld_dma_status_busy_get FOUND")
else:
    print("MISSING: lld_dma_status_busy_get")
    # Show STATUS block
    start = content.find('REGISTER: STATUS')
    end   = content.find('REGISTER:', start+1) if start != -1 else len(content)
    block = content[start:end]
    Path('debug_status_block.txt').write_text(block, encoding='utf-8', errors='replace')
    print("STATUS block written to debug_status_block.txt")

if '->stSTATUS.stNative.BUSY' in content:
    print("OK: struct path found")
else:
    print("MISSING: ->stSTATUS.stNative.BUSY")
