"""Sending a drafted purchase order to the rep: a plain mail with the lines and the same as a PDF.

A person presses Send; nothing here is called by the agent. The order is marked sent only after
the mail goes out (or, with no mailbox on the site, after it is printed to the log and the person
has been told so).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.prices import mail
from app.prices.models import SupplierRep
from app.restaurant.models import PurchaseOrder, Restaurant
from app.support.pdf import pdf_bytes


def rep_email(db: Session, order: PurchaseOrder) -> str | None:
    if not order.supplier_id:
        return None
    rep = db.query(SupplierRep).filter(SupplierRep.restaurant_id == order.restaurant_id, SupplierRep.supplier_id == order.supplier_id).first()
    return rep.email if rep and rep.email else None


def render(db: Session, order: PurchaseOrder, restaurant: Restaurant | None) -> tuple[str, str, list[str]]:
    """(subject, text body, PDF lines)."""
    name = (restaurant.name if restaurant else None) or "the restaurant"
    subject = f"Purchase order from {name}: {order.supplier_name or 'your account'}, {order.expected_on.isoformat() if order.expected_on else date.today().isoformat()}"
    rows: list[str] = []
    if order.inventory_item_id is not None:
        rows.append(f"{order.packs or order.quantity:g} {order.pack_unit or order.unit}  {order.item_name}")
    for line in order.lines:
        price = f"  @ {line.unit_price_expected:.2f}" if line.unit_price_expected is not None else ""
        rows.append(f"{line.qty:g} {line.unit}  {line.description}{price}")
    body = [f"Hello{',' if not order.supplier_name else ' ' + order.supplier_name + ','}", "",
            f"Please deliver the following to {name}" + (f" by {order.expected_on.strftime('%A %b %-d')}" if order.expected_on else "") + ":", ""]
    body += rows
    if order.estimated_cost:
        body += ["", f"Expected total about ${order.estimated_cost:,.2f} at the prices on our last invoices."]
    body += ["", "Thanks,", name]
    pdf_lines = [f"PURCHASE ORDER  {name}", f"To: {order.supplier_name or ''}", f"Expected: {order.expected_on.isoformat() if order.expected_on else ''}", ""] + rows
    return subject, "\n".join(body), pdf_lines


def send(db: Session, order: PurchaseOrder, to: list[str], restaurant: Restaurant | None) -> bool:
    subject, text, pdf_lines = render(db, order, restaurant)
    html = "<pre style=\"font-family:ui-monospace,Menlo,monospace;font-size:14px\">" + text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"
    pdf = pdf_bytes(pdf_lines)
    return mail.send(to, subject, html, text, attachments=[(f"purchase-order-{order.id[:8]}.pdf", "application/pdf", pdf)])
