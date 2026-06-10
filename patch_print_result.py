import sys
# force stdout to utf-8 so the script can print the chars
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

path = r'c:\Users\pavan\Desktop\cxl\sfr_gen\tests\test_llm_patch_from_sfr_diff.py'
content = open(path, encoding='utf-8').read()

box_chars = ['\u250c', '\u2502', '\u251c', '\u2514', '\u2500']
for ch in box_chars:
    n = content.count(ch)
    content = content.replace(ch, {'\u250c':'+', '\u2502':'|', '\u251c':'+', '\u2514':'+', '\u2500':'-'}[ch])
    print(f"Replaced {n} of U+{ord(ch):04X}")

open(path, 'w', encoding='utf-8').write(content)
print("Done")
