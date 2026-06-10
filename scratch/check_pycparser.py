from pycparser import c_parser
from pycparser.c_parser import _TokenStream
from lld_gen.ast_refactor import STANDARD_TYPEDEFS

class BetterCustomCParser(c_parser.CParser):
    def __init__(self, typedefs=None):
        super().__init__()
        self.custom_typedefs = typedefs or set()

    def parse(self, text, filename="", debug=False):
        # Initialize scope stack
        self._scope_stack = [dict()]
        
        # Register standard typedefs
        for t in STANDARD_TYPEDEFS:
            self._add_typedef_name(t, None)
        # Register custom typedefs
        for t in self.custom_typedefs:
            self._add_typedef_name(t, None)
            
        # Now input lexer text
        self.clex.input(text, filename)
        self._tokens = _TokenStream(self.clex)

        ast = self._parse_translation_unit_or_empty()
        tok = self._peek()
        if tok is not None:
            self._parse_error(f"before: {tok.value}", self._tok_coord(tok))
        return ast

parser = BetterCustomCParser({"uint8_t", "pSFR_PMU"})
try:
    parser.parse("struct foo { uint8_t x; };")
    print("BetterCustomCParser parsed uint8_t struct successfully!")
except Exception as e:
    print("BetterCustomCParser failed to parse uint8_t struct:", e)

try:
    parser.parse("struct lld_pmu { pSFR_PMU pSFR; };")
    print("BetterCustomCParser parsed pSFR_PMU struct successfully!")
except Exception as e:
    print("BetterCustomCParser failed to parse pSFR_PMU struct:", e)
