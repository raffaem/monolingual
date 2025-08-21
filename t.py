import json
import sys
from wikidict import parse

sys.path.insert(0, "wikitextprocessor/src")
from wikitextprocessor import Wtp

modules = json.loads(parse.get_output_file_modules(parse.get_source_dir("da"), "da", "da", "20250801").read_text())
ctx = Wtp(lang_code="da")


ctx.add_page("Modul:lang", 828, modules["Modul:lang"], model="Scrubindo")
ctx.add_page("Modul:lang/data", 828, modules["Modul:lang/data"], model="Scrubindo")

ctx.start_page("word")
p = ctx.parse("{{lang|fr}}")

print(p.children[0])
