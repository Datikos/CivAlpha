"""Rule-based (keyword) extraction of source-linked passages from filing HTML. Deterministic and inspectable; every
passage keeps its section heading and character offsets in the extracted text.

Element text follows jsoup's Element.text() (the Java backend's HTML library): whitespace (including NBSP) collapses
to single spaces, zero-width space and soft hyphen are dropped, block elements are separated by a space, and the
result is trimmed. Stored passages and offsets therefore match what the Java extractor produced.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import lxml.html
from lxml import etree

VERSION = "rules-v1"
MAX_PASSAGES = 60
MAX_CHARS = 1500
# "." as in Java: anything but a line terminator
_ITEM_HEADING = re.compile(r"(item\s+\d+[a-z]?\.?)([^\n\r\u0085\u2028\u2029]{0,160})", re.IGNORECASE | re.ASCII)
_HEADING_TAG = re.compile(r"h[1-4]")


@dataclass(frozen=True)
class Passage:
    section: str | None
    topic: str
    text: str
    char_start: int
    char_end: int


# Topic rules in priority order.
RULES: list[tuple[str, re.Pattern]] = [(topic, re.compile(rx, re.IGNORECASE | re.ASCII)) for topic, rx in (
    ("TRADE", "tariff|export control|trade restriction|import dut|trade polic|trade war|sanction|countervailing|anti-dumping"),
    ("GEOGRAPHIC_REVENUE", "(net sales|revenue|net revenue)[^.]{0,120}(geographic|countr|region|china|europe|japan|taiwan|mexico|asia)"
                           "|attributed to countries|by geographic"),
    ("SEGMENT", "reportable segment|segment information|operating segment"),
    ("RATES", "interest rate|basis point|floating[- ]rate|variable[- ]rate"),
    ("DEBT", "long-term debt|senior notes|notes due|credit facility|commercial paper|borrowings"),
    ("COSTS", "cost of (sales|revenue|goods)|raw material|commodit|component (cost|shortage)|supplier|manufactur|assembl|foundr"),
    ("RISK", "could (adversely|materially|negatively) affect|adverse effect"),
)]

# ---------------------------------------------------------------------------------------------------- jsoup text()
_BLOCK = frozenset(
    "address applet article aside blockquote body caption center col colgroup dd details dialog dir div dl dt fieldset "
    "figcaption figure footer form frame frameset h1 h2 h3 h4 h5 h6 head header hgroup hr html li link listing main "
    "marquee menu meta nav noframes noscript ol p plaintext pre script search section style table tbody td template "
    "tfoot th thead title tr ul".split())
_TEXT_BOUNDARY = frozenset(
    "audio button canvas embed iframe img input meter object option output picture progress select textarea video".split())
_PRESERVE = frozenset("plaintext pre script textarea title".split())
_WS = frozenset(" \t\n\f\r ")
_INVISIBLE = frozenset("​­")


def _is_element(n) -> bool:
    return isinstance(n, etree._Element) and isinstance(n.tag, str)


def _children(el):
    """jsoup child nodes: text nodes (str) interleaved with element/comment nodes."""
    out = []
    if el.text:
        out.append(el.text)
    for c in el:
        out.append(c)
        if c.tail:
            out.append(c.tail)
    return out


def _has_text(el) -> bool:
    for n in el.iter():
        if n is not el and n.tail and any(ch not in _WS for ch in n.tail):
            return True
        if _is_element(n) and n.text and any(ch not in _WS for ch in n.text):
            return True
    return False


def _preserve_ws(el) -> bool:
    for _ in range(6):
        if el is None:
            return False
        if el.tag in _PRESERVE:
            return True
        el = el.getparent()
    return False


class _Accum:
    def __init__(self):
        self.parts: list[str] = []
        self.last = ""

    def add(self, s: str) -> None:
        if s:
            self.parts.append(s)
            self.last = s[-1]

    def __bool__(self) -> bool:
        return bool(self.last)

    def normalised(self, s: str) -> None:
        out, last_white, reached = [], False, False
        strip_leading = self.last == " "
        for ch in s:
            if ch in _WS:
                if (strip_leading and not reached) or last_white:
                    continue
                out.append(" ")
                last_white = True
            elif ch not in _INVISIBLE:
                out.append(ch)
                last_white = False
                reached = True
        self.add("".join(out))


def element_text(root) -> str:
    """jsoup Element.text(): the normalised, combined text of the element and its descendants."""
    acc = _Accum()

    def head(el) -> None:
        if acc and acc.last != " " and (el.tag in _BLOCK or el.tag == "br"
                                         or (el.tag in _TEXT_BOUNDARY and len(_children(el)) > 0 and _has_text(el))):
            acc.add(" ")

    def tail(el, nxt) -> None:
        inline = el.tag not in _BLOCK
        needs = el.tag in _TEXT_BOUNDARY or not inline or any(_is_element(c) and c.tag in _BLOCK for c in el)
        if needs and (isinstance(nxt, str) or (_is_element(nxt) and nxt.tag not in _BLOCK)) and acc.last != " ":
            acc.add(" ")

    def next_sibling(el):
        if el.tail:
            return el.tail
        return el.getnext()

    # iterative depth-first traversal: ("enter", node, parent) / ("exit", element)
    stack: list = [("exit", root), ("enter", root, None)]
    while stack:
        item = stack.pop()
        if item[0] == "exit":
            el = item[1]
            tail(el, next_sibling(el))
            continue
        node, parent = item[1], item[2]
        if isinstance(node, str):
            if _preserve_ws(parent):
                acc.add(node)
            else:
                acc.normalised(node)
        elif _is_element(node):
            head(node)
            if node is not root:
                stack.append(("exit", node))
            for c in reversed(_children(node)):
                stack.append(("enter", c, node))
        # comments and processing instructions contribute nothing
    return _java_trim("".join(acc.parts))


def _java_trim(s: str) -> str:
    i, j = 0, len(s)
    while i < j and s[i] <= " ":
        i += 1
    while j > i and s[j - 1] <= " ":
        j -= 1
    return s[i:j]


# ---------------------------------------------------------------------------------------------------- extraction
def _parse(html: str):
    parser = lxml.html.HTMLParser(encoding="utf-8", huge_tree=True)
    try:
        return lxml.html.document_fromstring(html.encode("utf-8"), parser=parser)
    except etree.ParserError:   # empty document
        return None


_TABLE_PARTS = frozenset("td th tr tbody thead tfoot caption col colgroup".split())


def _remove(doc) -> None:
    """Drops script, style and ix:header subtrees (keeping the text that follows them). Table-part tags outside any
    table are unwrapped, as an HTML5 parser (jsoup) ignores them while libxml2 keeps them as elements."""
    for el in [e for e in doc.iter() if _is_element(e)]:
        if el.tag in ("script", "style", "ix:header"):
            el.drop_tree()
        elif el.tag in _TABLE_PARTS and not any(a.tag == "table" for a in el.iterancestors()):
            el.drop_tag()


def _candidates(doc) -> list:
    """Elements matching "h1, h2, h3, h4, p, div:not(:has(div, p)), td:not(:has(div, p, table))" in document order."""
    elements = [e for e in doc.iter() if _is_element(e)]
    below: dict = {}   # element -> tag names among its descendants (only div/p/table matter)
    for e in reversed(elements):
        s = set()
        for c in e:
            if _is_element(c):
                s |= below.get(c, set())
                if c.tag in ("div", "p", "table"):
                    s.add(c.tag)
        below[e] = s
    out = []
    for e in elements:
        t = e.tag
        if t in ("h1", "h2", "h3", "h4", "p"):
            out.append(e)
        elif t == "div" and not (below[e] & {"div", "p"}):
            out.append(e)
        elif t == "td" and not below[e]:
            out.append(e)
    return out


def extract(html: str) -> list[Passage]:
    doc = _parse(html)
    if doc is None:
        return []
    _remove(doc)
    text_len = 0
    out: list[Passage] = []
    seen: set[str] = set()
    section = None
    for el in _candidates(doc):
        t = _java_trim(element_text(el).replace(" ", " "))
        if not t:
            continue
        heading = bool(_HEADING_TAG.fullmatch(el.tag)) or (len(t) < 200 and _ITEM_HEADING.fullmatch(t) is not None)
        start = text_len
        text_len += len(t) + 1
        if heading:
            section = t[:200] if len(t) > 200 else t
            continue
        if len(t) < 60:
            continue
        for topic, pattern in RULES:
            if pattern.search(t):
                body = t[:MAX_CHARS] + "…" if len(t) > MAX_CHARS else t
                if body not in seen:
                    seen.add(body)
                    out.append(Passage(section, topic, body, start, start + len(t)))
                break
        if len(out) >= MAX_PASSAGES:
            break
    return out
