"""Five sample foodservice invoices as PDFs, in fixtures/invoices/.

Two suppliers a small restaurant might buy the same things from -- Worldwide
Produce and Chef's Warehouse -- with overlapping items (kale, broccoli, olive
oil, 16 oz deli containers, bread) and the pack strings printed the way they
print them ("24 CT", "4/1 GAL", "CS 12"). One of the five has a total that
doesn't match its lines, on purpose: it's what "needs review" is for.

The PDFs are written by hand, uncompressed, so the scripted reader
(app/prices/extract.py) can read them back without a PDF library. Run:

    python scripts/make_fixture_invoices.py
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "invoices"

#: (supplier, address line, date, invoice number, [(description, qty, pack, unit price)], printed total or None)
INVOICES = [
    ("Worldwide Produce", "2468 Market St, Los Angeles CA", "10/07/2026", "WP-10007", [
        ("KALE GREEN 24 CT", 10, "CS", 29.40),
        ("BROCCOLI CROWNS CS 12", 6, "CS", 27.90),
        ("OLIVE OIL EV 4/1 GAL", 2, "CS", 66.00),
        ("DELI CONTAINER 16 OZ 500 CT", 1, "CS", 54.90),
        ("BREAD CIABATTA 24 CT", 3, "CS", 26.40),
    ], None),
    ("Chef's Warehouse", "1 Harbor Way, Vernon CA", "10/10/2026", "CW-55010", [
        ("KALE, GREEN 24CT", 8, "CS", 27.60),
        ("BROCCOLI CROWN 12 CT CS", 6, "CS", 30.10),
        ("EVOO 4/1GAL", 2, "CS", 64.00),
        ("CONTAINER DELI 16OZ 500CT", 1, "CS", 49.80),
        ("BREAD CIABATTA 24CT", 3, "CS", 28.00),
    ], None),
    ("Worldwide Produce", "2468 Market St, Los Angeles CA", "10/14/2026", "WP-10014", [
        ("KALE GREEN 24 CT", 10, "CS", 30.60),
        ("BROCCOLI CROWNS CS 12", 6, "CS", 27.90),
        ("OLIVE OIL EV 4/1 GAL", 2, "CS", 66.00),
        ("BREAD CIABATTA 24 CT", 3, "CS", 26.40),
    ], None),
    # The lines add to 537.10; the printed total says 542.10. Needs a check.
    ("Chef's Warehouse", "1 Harbor Way, Vernon CA", "10/17/2026", "CW-55017", [
        ("KALE, GREEN 24CT", 8, "CS", 27.60),
        ("BROCCOLI CROWN 12 CT CS", 6, "CS", 30.10),
        ("EVOO 4/1GAL", 1, "CS", 64.00),
        ("CONTAINER DELI 16OZ 500CT", 1, "CS", 49.80),
    ], 542.10),
    ("Worldwide Produce", "2468 Market St, Los Angeles CA", "10/21/2026", "WP-10021", [
        ("KALE GREEN 24 CT", 10, "CS", 31.20),
        ("BROCCOLI CROWNS CS 12", 6, "CS", 28.50),
        ("OLIVE OIL EV 4/1 GAL", 2, "CS", 68.00),
        ("DELI CONTAINER 16 OZ 500 CT", 1, "CS", 54.90),
        ("BREAD CIABATTA 24 CT", 3, "CS", 26.40),
    ], None),
]


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_lines(supplier, address, day, number, lines, printed_total) -> list[str]:
    total = printed_total if printed_total is not None else round(sum(q * p for _, q, _, p in lines), 2)
    out = [supplier, address, "", f"INVOICE #{number}", f"Date: {day}", "Sold to: Botanica, 410 Olive St", "",
           f"{'DESCRIPTION':<30}{'QTY':>5}  {'PACK':<4}  {'PRICE':>8}  {'AMOUNT':>9}"]
    for description, qty, pack, price in lines:
        out.append(f"{description:<30}{qty:>5}  {pack:<4}  {price:>8.2f}  {qty * price:>9.2f}")
    out += ["", f"{'Total':<30}{'':>5}  {'':<4}  {'':>8}  {total:>9.2f}", "", "Thank you for your business."]
    return out


def pdf_bytes(text_lines: list[str]) -> bytes:
    """A one-page PDF in Courier, one Tj per line, nothing compressed."""
    content_lines = ["BT", "/F1 10 Tf", "12 TL", "40 760 Td"]
    for line in text_lines:
        content_lines.append(f"({_escape(line)}) Tj T*")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def filename(supplier: str, day: str) -> str:
    month, dd, year = day.split("/")
    slug = supplier.lower().replace("'", "").replace(" ", "-")
    return f"{slug}-{year}-{month}-{dd}.pdf"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for supplier, address, day, number, lines, printed_total in INVOICES:
        path = OUT / filename(supplier, day)
        path.write_bytes(pdf_bytes(render_lines(supplier, address, day, number, lines, printed_total)))
        print(path.relative_to(OUT.parent.parent))


if __name__ == "__main__":
    main()
