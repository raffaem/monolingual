"""Parse and store raw Wiktionary data."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import timedelta
from html import unescape
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

import sys
sys.path.insert(0, "../wikitextprocessor/src")
from wikitextprocessor import Wtp

from . import lang, utils

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator


log = logging.getLogger(__name__)

RE_TEXT = re.compile(r"<text[^>]*>(.*)</text>", flags=re.DOTALL).finditer
RE_NS = re.compile(r"<ns>(\d+)</ns>").finditer
RE_TITLE_WORD = re.compile(r"<title>([^:]*)</title>").finditer

# To list all words not taken into account with current head sections:
#    DEBUG_PARSE=1 python -m wikidict LOCALE --parse >out.log
DEBUG_PARSE = "DEBUG_PARSE" in os.environ


def xml_iter_parse(file: Path) -> Generator[str]:
    """Efficient XML parsing for big files."""
    element: list[str] = []
    is_element = False

    with file.open(encoding="utf-8") as fh:
        for line in fh:
            if is_element:
                if "/page>" in line:
                    yield "".join(element)
                    element = []
                    is_element = False
                else:
                    element.append(line)
            elif "<page" in line:
                is_element = True


def xml_parse_element(
    element: str,
    head_sections_matcher: Callable[[str], Iterator[str]],
    module_matcher: Callable[[str], Iterator[re.Match[str]]],
    template_matcher: Callable[[str], Iterator[re.Match[str]]],
    wtp_ctx: Wtp,
) -> tuple[str, str]:
    """Parse the XML `element` to retrieve the word and its definitions."""
    if title := next(module_matcher(element), None):
        for text in RE_TEXT(element, pos=element.find("<text")):
            # ns = next(RE_NS(element))
            wtp_ctx.add_page(title[1], 0, body=text[1], model="Scribunto")

    elif title := next(template_matcher(element), None):
        for text in RE_TEXT(element, pos=element.find("<text")):
            # ns = next(RE_NS(element))
            wtp_ctx.add_page(title[1], 0, body=text[1], model="wikitext")

    elif title := next(RE_TITLE_WORD(element), None):
        for text in RE_TEXT(element, pos=element.find("<text", title.endpos)):
            if next(head_sections_matcher(wikicode := text[1]), None):
                return title[1], wikicode

        if DEBUG_PARSE:
            try:
                print(f"{title[1]!r}: {wikicode[:200]!r}", flush=True)
            except UnboundLocalError:
                print(f"{title[1]!r}: NO TEXT", flush=True)

    # No Wikicode; unfinished page; no interesting head section; a foreign word, etc. Who knows?
    return "", ""


def process(file: Path, locale: str, db_path: Path) -> dict[str, str]:
    """Process the big XML file and retain only information we are interested in."""
    words: dict[str, str] = {}
    lang_src, lang_dst = utils.guess_locales(locale, use_log=False)

    log.info("Processing %s for destination lang %r ...", file, lang_dst)

    if lang_src == "de":
        # It is not possible to use a regexp matcher
        def head_sections_matcher(wikicode: str) -> Iterator[str]:
            return (s for s in lang.head_sections[lang_dst] if s in wikicode.lower())
    else:
        head_sections_matcher = re.compile(
            rf"^=*\s*(?:{'|'.join(hs.replace('{', r'\{').replace('|', r'\|') for hs in lang.head_sections[lang_dst])})",
            flags=re.IGNORECASE | re.MULTILINE,
        ).finditer  # type: ignore[assignment]

    module_matcher = re.compile(rf"<title>({lang.module_trans[lang_dst]}:[^<]+)</title>").finditer
    template_matcher = re.compile(rf"<title>({lang.template_trans[lang_dst]}:[^<]+)</title>").finditer
    wtp_ctx = Wtp(db_path, lang_code=lang_dst)

    for element in xml_iter_parse(file):
        title, code = xml_parse_element(element, head_sections_matcher, module_matcher, template_matcher, wtp_ctx)
        if not title or not code or (lang_dst == "en" and title[:19] == "Unsupported titles/"):
            continue
        words[unescape(title)] = unescape(code)

    wtp_ctx.close_db_conn()
    return words


def save(output: Path, words: dict[str, str]) -> None:
    """Persist data."""
    if not words:
        log.warning("No words to save.")
        return

    output.parent.mkdir(exist_ok=True, parents=True)
    with output.open(mode="w", encoding="utf-8") as fh:
        json.dump(words, fh, ensure_ascii=False, indent=4, sort_keys=True)

    log.info("Saved %s words into %s", f"{len(words):,}", output)


def save_modules(output: Path, modules: dict[str, str]) -> None:
    """Persist data."""
    with output.open(mode="w", encoding="utf-8") as fh:
        json.dump(modules, fh, check_circular=False, ensure_ascii=False, indent=4, sort_keys=True)

    log.info("Saved %s modules into %s", f"{len(modules):,}", output)


def get_latest_xml_file(source_dir: Path) -> Path | None:
    """Get the name of the last pages-*.xml file."""
    files = list(source_dir.glob(f"pages-{'[0-9]' * 8}.xml"))
    return sorted(files)[-1] if files else None


def get_source_dir(lang_src: str) -> Path:
    return Path(os.getenv("CWD", "")) / "data" / lang_src


def get_output_file(source_dir: Path, lang_src: str, lang_dst: str, snapshot: str) -> Path:
    return source_dir.parent / lang_dst / lang_src / f"data_wikicode-{snapshot}.json"


# def get_output_file_modules(source_dir: Path, lang_src: str, lang_dst: str, snapshot: str) -> Path:
#     return source_dir.parent / lang_dst / lang_src / f"modules-{snapshot}.json"


def get_output_file_modules(source_dir: Path, lang_src: str, lang_dst: str, snapshot: str) -> Path:
    return source_dir.parent / lang_dst / lang_src / f"modules-{snapshot}.sqlite"


def main(locale: str) -> int:
    """Entry point."""

    start = monotonic()
    lang_src, lang_dst = utils.guess_locales(locale)

    source_dir = get_source_dir(lang_src)
    if not (input_file := get_latest_xml_file(source_dir)):
        log.error("No dump found. Run with --download first ... ")
        return 1

    ret = 0
    snapshot = input_file.stem.split("-")[-1]
    output = get_output_file(source_dir, lang_src, lang_dst, snapshot)
    if output.is_file():
        log.info("Already parsed into %s", output)
    else:
        db = get_output_file_modules(source_dir, lang_src, lang_dst, snapshot)
        words = process(input_file, locale, db)
        # save_modules(get_output_file_modules(source_dir, lang_src, lang_dst, snapshot), modules)
        save(output, words)
        if not words:
            ret = 1

    log.info("Parse done in %s!", timedelta(seconds=monotonic() - start))
    return ret
