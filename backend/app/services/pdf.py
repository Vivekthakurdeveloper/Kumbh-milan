"""Minimal tabular PDF writer for MIS reports (PDF 1.4, built-in Courier fonts, no external dependency).

Report rows hold codes, enum values, and timestamps only, so text is limited to printable ASCII; anything else
is replaced with '?'. Columns are fixed-width and wide values are truncated with '~'.
"""

from __future__ import annotations

PAGE_WIDTH, PAGE_HEIGHT = 842, 595  # A4 landscape, points
MARGIN = 30
FONT_SIZE = 7
LEADING = 9
CHAR_WIDTH = FONT_SIZE * 0.6  # Courier advance width
MAX_LINE_CHARS = int((PAGE_WIDTH - 2 * MARGIN) / CHAR_WIDTH)
MAX_COLUMN_CHARS = 32
HEADER_LINES = 4  # title, subtitle, column header, rule
FOOTER_LINES = 2
LINES_PER_PAGE = int((PAGE_HEIGHT - 2 * MARGIN) / LEADING) - HEADER_LINES - FOOTER_LINES


def _ascii(value: str) -> str:
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in value)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _fit(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 1] + "~"


def _widths(header: list[str], rows: list[list[str]]) -> list[int]:
    widths = [len(h) for h in header]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    widths = [min(w, MAX_COLUMN_CHARS) for w in widths]
    while sum(widths) + 2 * (len(widths) - 1) > MAX_LINE_CHARS and max(widths) > 8:
        widest = widths.index(max(widths))
        widths[widest] -= 1
    return widths


def _line(values: list[str], widths: list[int]) -> str:
    return "  ".join(_fit(v, w).ljust(w) for v, w in zip(values, widths, strict=True)).rstrip()


def _page_stream(title: str, subtitle: str, header_line: str, rule: str, body: list[str], footer: str) -> bytes:
    top = PAGE_HEIGHT - MARGIN - FONT_SIZE
    parts = [f"BT /F2 {FONT_SIZE + 2} Tf {MARGIN} {top} Td ({_escape(title)}) Tj ET",
             f"BT /F1 {FONT_SIZE} Tf {LEADING} TL {MARGIN} {top - LEADING - 2} Td ({_escape(subtitle)}) Tj ET",
             f"BT /F2 {FONT_SIZE} Tf {LEADING} TL {MARGIN} {top - 3 * LEADING} Td ({_escape(header_line)}) Tj "
             f"T* /F1 {FONT_SIZE} Tf ({_escape(rule)}) Tj"]
    parts.extend(f"T* ({_escape(line)}) Tj" for line in body)
    parts.append("ET")
    parts.append(f"BT /F1 {FONT_SIZE} Tf {MARGIN} {MARGIN} Td ({_escape(footer)}) Tj ET")
    return "\n".join(parts).encode("latin-1")


def render_table(title: str, subtitle: str, header: list[str], rows: list[list[str]]) -> bytes:
    """Renders a paginated fixed-width table. Every page repeats the title, subtitle, and column header."""
    title, subtitle = _ascii(title), _ascii(subtitle)
    header = [_ascii(h) for h in header]
    rows = [[_ascii(v) for v in row] for row in rows]
    widths = _widths(header, rows)
    header_line = _line(header, widths)
    rule = "-" * min(len(header_line), MAX_LINE_CHARS)
    lines = [_line(row, widths) for row in rows] or ["(no rows for this period)"]
    pages = [lines[i : i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)]

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # pages tree, filled in once page object numbers are known
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>",
    ]
    page_refs = []
    for number, body in enumerate(pages, start=1):
        stream = _page_stream(title, subtitle, header_line, rule, body, f"Page {number} of {len(pages)}")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_ref = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (PAGE_WIDTH, PAGE_HEIGHT, content_ref)
        )
        page_refs.append(len(objects))
    kids = " ".join(f"{ref} 0 R" for ref in page_refs).encode()
    objects[1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(page_refs)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)
