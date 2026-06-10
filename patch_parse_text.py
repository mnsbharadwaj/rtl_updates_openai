path = r'c:\Users\pavan\Desktop\cxl\sfr_gen\lld_gen\sfr_diff_analyzer.py'
content = open(path, encoding='utf-8').read()

TARGET = (
    '    def parse_text(self, text: str, source: str = "<string>") -> SfrIR:\n'
    '        ir = SfrIR(ip=self.ip, source=source)\n'
    '        lines = text.splitlines()\n'
    '\n'
    '        # Step 1: Collect all defines\n'
)

REPLACEMENT = (
    '    def parse_text(self, text: str, source: str = "<string>") -> SfrIR:\n'
    '        # Auto-detect format and delegate to the right sub-parser\n'
    '        fmt = _detect_sfr_format(text)\n'
    '        if fmt == "volatile_union":\n'
    '            return VolatileUnionSfrParser(ip=self.ip).parse_text(text, source=source)\n'
    '        if fmt == "native_union":\n'
    '            return NativeUnionSfrParser(ip=self.ip).parse_text(text, source=source)\n'
    '        # Legacy #define MASK/SHIFT parser\n'
    '        ir = SfrIR(ip=self.ip, source=source)\n'
    '        lines = text.splitlines()\n'
    '\n'
    '        # Step 1: Collect all defines\n'
)

count = content.count(TARGET)
print(f'Occurrences of target: {count}')
if count == 1:
    content2 = content.replace(TARGET, REPLACEMENT, 1)
    open(path, 'w', encoding='utf-8').write(content2)
    print('Patch applied successfully.')
else:
    print('ERROR: target not found or ambiguous. Showing context...')
    # Find SfrParser class and show parse_text
    idx = content.find('class SfrParser:')
    print(repr(content[idx:idx+800]))
