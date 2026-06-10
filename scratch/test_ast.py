import sys
from pycparser import c_parser, c_ast

class CustomCParser(c_parser.CParser):
    def __init__(self, typedefs=None):
        super().__init__()
        self.custom_typedefs = typedefs or []

    def _reset(self, *args, **kwargs):
        super()._reset(*args, **kwargs)
        for t in self.custom_typedefs:
            self._add_typedef_name(t, None)

def remove_comments_keep_spacing(text: str) -> str:
    out = list(text)
    n = len(out)
    i = 0
    while i < n:
        if i + 1 < n and out[i] == '/' and out[i+1] == '*':
            out[i] = ' '
            out[i+1] = ' '
            i += 2
            while i < n:
                if i + 1 < n and out[i] == '*' and out[i+1] == '/':
                    out[i] = ' '
                    out[i+1] = ' '
                    i += 2
                    break
                else:
                    if out[i] != '\n' and out[i] != '\r':
                        out[i] = ' '
                    i += 1
        elif i + 1 < n and out[i] == '/' and out[i+1] == '/':
            out[i] = ' '
            out[i+1] = ' '
            i += 2
            while i < n and out[i] != '\n' and out[i] != '\r':
                out[i] = ' '
                i += 1
        else:
            i += 1
    return "".join(out)

# Initialize custom parser
parser = CustomCParser(["uint8_t"])

c_code = """#include <stdint.h>
#define REG_VAL 0x55

// Comment describing function
static inline void lld_pmu_pmu_con_dma_en_set(struct lld_pmu *lld, uint8_t val)
{
    /* Keep this spacing */
    lld->pSFR->stPMU_CON.stNative.DMA_EN = val;
}
"""

try:
    # 1. Clean preprocessor
    cleaned_lines = []
    for line in c_code.splitlines():
        if line.strip().startswith('#'):
            cleaned_lines.append("// " + line)
        else:
            cleaned_lines.append(line)
    code_no_macros = "\n".join(cleaned_lines)
    
    # 2. Clean comments
    code_parsed = remove_comments_keep_spacing(code_no_macros)
    
    # 3. Parse
    ast = parser.parse(code_parsed, filename='<test>')
    print("Parsing succeeded with comment stripping!")
    
except Exception as e:
    print("Failed:", e)
