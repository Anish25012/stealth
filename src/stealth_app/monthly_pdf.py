"""The monthly report as a PDF, to keep, print or show someone. Same numbers as the page.

ReportLab draws it with the PDF's built-in fonts, so nothing extra needs installing. Company names
come from the registry or the person, and replies never appear here, but everything is escaped
before ReportLab's mini-markup sees it all the same.
"""

from __future__ import annotations

import io
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from stealth.templates.render import format_date
from stealth_app.monthly import Monthly

ICON = Path(__file__).parent / "assets" / "icon.png"

INK = colors.HexColor("#15181c")
MUTED = colors.HexColor("#5b636e")
LINE = colors.HexColor("#e3e6ea")
ACCENT = colors.HexColor("#0e7a70")
PANEL = colors.HexColor("#f4f5f7")
TONES = {
    "good": (colors.HexColor("#e2f4e9"), colors.HexColor("#0f5f37")),
    "waiting": (colors.HexColor("#e5edfa"), colors.HexColor("#1d4c99")),
    "action": (colors.HexColor("#fcefd3"), colors.HexColor("#864700")),
    "problem": (colors.HexColor("#fce6e6"), colors.HexColor("#a11a28")),
    "neutral": (colors.HexColor("#eceef1"), colors.HexColor("#3d444d")),
}


def _style(
    name: str, size: float, *, leading: float | None = None, **extra: object
) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName=str(extra.pop("font", "Helvetica")),
        fontSize=size,
        leading=leading or size * 1.35,
        textColor=extra.pop("color", INK),
        **extra,
    )


TITLE = _style("title", 22, leading=26, font="Helvetica-Bold")
SUB = _style("sub", 10, color=MUTED)
H2 = _style("h2", 13, leading=17, font="Helvetica-Bold", spaceBefore=6, spaceAfter=4)
BODY = _style("body", 10)
SMALL = _style("small", 8.5, color=MUTED)
FIGURE = _style("figure", 30, leading=32, font="Helvetica-Bold", color=ACCENT)
LABEL = _style("label", 8.5, color=MUTED)
STAT = _style("stat", 16, leading=19, font="Helvetica-Bold")
CELL = _style("cell", 9)
CELL_BOLD = _style("cellb", 9, font="Helvetica-Bold")
RIGHT = _style("right", 8.5, color=MUTED, alignment=TA_RIGHT)


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


def _badge(label: str, tone: str) -> Table:
    background, foreground = TONES.get(tone, TONES["neutral"])
    width = stringWidth(label, "Helvetica-Bold", 8) + 12  # as wide as its words, like the app's
    badge = Table(
        [[_p(label, _style("b", 8, color=foreground, font="Helvetica-Bold"))]], colWidths=[width]
    )
    badge.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("ROUNDEDCORNERS", [4, 4, 4, 4]),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    badge.hAlign = "LEFT"
    return badge


def _score_text(report: Monthly) -> str:
    change = report.score_change
    if change is None:
        return "Stealth started this month."
    since = f"since 1 {report.month.name}"
    if change > 0:
        return f"Up {change} {since}."
    if change < 0:
        return f"Down {-change} {since}."
    return f"No change {since}."


def _panel(report: Monthly, width: float) -> Table:
    figure = [
        _p("Exposure score", LABEL),
        Paragraph(f"{report.score_end.value}<font size=11 color='#5b636e'> / 100</font>", FIGURE),
        _p(report.score_end.words + ".", BODY),
        _p(_score_text(report), SMALL),
    ]
    if not report.score_end.listings_checked:
        figure.append(_p("People-search listings weren't checked yet.", SMALL))
    done = [
        _p("Companies done", LABEL),
        Paragraph(f"{report.done}<font size=11 color='#5b636e'> of {report.active}</font>", FIGURE),
        _p(f"{report.percent}% removed you or hold nothing.", BODY),
    ]
    panel = Table([[figure, done]], colWidths=[width / 2, width / 2])
    panel.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("ROUNDEDCORNERS", [8, 8, 8, 8]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    return panel


def _stats(report: Monthly, width: float) -> Table:
    items = [
        (report.letters, "removal requests sent"),
        (report.replies, "replies received"),
        (report.confirmed, "confirmed removals"),
        (report.complaints, "formal complaints"),
        (report.listings_found, "listings found"),
    ]
    row = [[_p(str(n), STAT), _p(label, LABEL)] for n, label in items]
    table = Table([row], colWidths=[width / len(items)] * len(items))
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("LINEABOVE", (0, 0), (-1, 0), 0.6, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _companies(report: Monthly, width: float) -> Table:
    rows: list[list[object]] = [
        [
            _p("Company", LABEL),
            _p("Now" if report.so_far else "At the end of the month", LABEL),
            _p("This month", LABEL),
        ]
    ]
    for line in report.lines:
        happened = (
            "<br/>".join(
                f"<font color='#5b636e'>{h.on.day} {h.on:%b}</font>&nbsp;&nbsp;{escape(h.text)}"
                for h in line.happenings
            )
            or "<font color='#5b636e'>Nothing new</font>"
        )
        rows.append(
            [
                _p(line.name, CELL_BOLD),
                _badge(line.info.label, line.info.tone),
                Paragraph(happened, CELL),
            ]
        )
    table = Table(rows, colWidths=[width * 0.28, width * 0.27, width * 0.45], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _footer(canvas: Canvas, doc: SimpleDocTemplate) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(
        doc.leftMargin,
        12 * mm,
        "Made by Stealth on your own computer. This report wasn't sent anywhere.",
    )
    canvas.drawRightString(A4[0] - doc.rightMargin, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render(report: Monthly) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
        title=f"Stealth report: {report.month.label}",
        author="Stealth",
        subject=f"Monthly data removal report for {report.person}",
    )
    width = doc.width - 12  # the frame pads text by 6pt a side: line tables up with it
    period = (
        f"So far this month, up to {format_date(report.made_on)}"
        if report.so_far
        else f"The whole of {report.month.label}"
    )
    header = Table(
        [
            [
                Image(str(ICON), width=11 * mm, height=11 * mm),
                [
                    _p(f"Your {report.month.label} report", TITLE),
                    _p(f"For {report.person}. {period}.", SUB),
                ],
                _p(f"Made {format_date(report.made_on)}", RIGHT),
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
    story += [_p(report.headline, _style("lead", 11.5, leading=16)), Spacer(1, 4 * mm)]
    story += [_panel(report, width), Spacer(1, 4 * mm), _stats(report, width), Spacer(1, 5 * mm)]
    if report.needs:
        needs = [_p("Waiting on you", H2)]
        needs += [
            Paragraph(f"&bull;&nbsp;<b>{escape(n.name)}</b>: {escape(n.info.label)}", BODY)
            for n in report.needs
        ]
        needs.append(_p("Open Stealth to deal with these.", SMALL))
        story += [KeepTogether(needs), Spacer(1, 5 * mm)]
    story += [_p("Company by company", H2)]
    if report.lines:
        story.append(_companies(report, width))
    else:
        story.append(_p("Stealth hadn't written to any companies yet.", BODY))
    story += [
        Spacer(1, 4 * mm),
        _p(
            "The score starts at 100 and loses points for what's still out there: 10 for each "
            "listing still up (at most 40), 5 for each company that refused (at most 25) and 1 "
            "for each company that hasn't confirmed yet (at most 20). Every letter and reply is "
            "kept in Stealth, with evidence packs on the Reports page.",
            SMALL,
        ),
    ]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
