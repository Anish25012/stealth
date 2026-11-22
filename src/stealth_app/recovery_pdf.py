"""An incident log as a PDF: for a bank, a lender, the police or your own records.

Drawn with ReportLab, like the monthly report, using its styles. Everything the person typed is
escaped before ReportLab's mini-markup sees it.
"""

from __future__ import annotations

import io
from datetime import date
from html import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from stealth.protect.incidents import Incident
from stealth.templates.render import format_date
from stealth_app.monthly_pdf import (
    CELL,
    CELL_BOLD,
    H2,
    ICON,
    LINE,
    MUTED,
    PANEL,
    RIGHT,
    SMALL,
    SUB,
    TITLE,
)


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _grid(rows: list[list[object]], widths: list[float]) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), PANEL),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _footer(canvas: Canvas, doc: SimpleDocTemplate) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(
        doc.leftMargin, 12 * mm, "Made by Stealth on your own computer. It wasn't sent anywhere."
    )
    canvas.drawRightString(A4[0] - doc.rightMargin, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render(incident: Incident, person: str, made_on: date) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
        title=f"Stealth incident log: {incident.situation.title}",
        author="Stealth",
        subject=f"Incident log for {person}",
    )
    width = doc.width - 12
    status = (
        f"Sorted on {format_date(incident.closed_on)}"
        if incident.closed_on
        else f"{incident.done} of {incident.total} steps done"
    )
    header = Table(
        [
            [
                Image(str(ICON), width=11 * mm, height=11 * mm),
                [
                    _p("Incident log", TITLE),
                    _p(
                        f"{incident.situation.title}. For {person}. Started "
                        f"{format_date(incident.started_on)}. {status}.",
                        SUB,
                    ),
                ],
                _p(f"Made {format_date(made_on)}", RIGHT),
            ]
        ],
        colWidths=[15 * mm, width - 55 * mm, 40 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story: list[object] = [header, Spacer(1, 6 * mm)]

    story.append(_p("Steps", H2))
    rows: list[list[object]] = [
        [_p("Step", CELL_BOLD), _p("Done", CELL_BOLD), _p("Reference and notes", CELL_BOLD)]
    ]
    for p in incident.progress:
        notes = "\n".join(x for x in (f"Ref: {p.reference}" if p.reference else "", p.note) if x)
        rows.append(
            [
                _p(p.action.title, CELL),
                _p(format_date(p.done_on) if p.done_on else "Not yet", CELL),
                _p(notes, CELL),
            ]
        )
    story += [_grid(rows, [width * 0.38, width * 0.2, width * 0.42]), Spacer(1, 5 * mm)]

    story.append(_p("What happened, in order", H2))
    rows = [[_p("Date", CELL_BOLD), _p("What happened", CELL_BOLD), _p("Reference", CELL_BOLD)]]
    for entry in incident.entries:
        rows.append(
            [
                _p(format_date(entry.happened_on), CELL),
                _p(entry.text, CELL),
                _p(entry.reference, CELL),
            ]
        )
    story.append(_grid(rows, [width * 0.2, width * 0.55, width * 0.25]))
    story += [
        Spacer(1, 4 * mm),
        _p(
            "Keep this with any letters or emails about it. Reference numbers help your bank, "
            "lenders and the police link your reports together.",
            SMALL,
        ),
    ]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
