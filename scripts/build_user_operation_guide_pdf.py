from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "USER_OPERATION_GUIDE.md"
OUTPUT = ROOT / "output" / "pdf" / "keiba-ai-pro-user-operation-guide.pdf"


def register_fonts() -> tuple[str, str]:
    regular = Path(r"C:\Windows\Fonts\NotoSansJP-VF.ttf")
    serif = Path(r"C:\Windows\Fonts\NotoSerifJP-VF.ttf")
    if not regular.exists():
        raise FileNotFoundError(f"Japanese font not found: {regular}")
    pdfmetrics.registerFont(TTFont("NotoSansJP", str(regular)))
    if serif.exists():
        pdfmetrics.registerFont(TTFont("NotoSerifJP", str(serif)))
        return "NotoSansJP", "NotoSerifJP"
    return "NotoSansJP", "NotoSansJP"


def inline_markup(text: str) -> str:
    text = html.escape(text.strip())
    text = re.sub(r"`([^`]+)`", r'<font name="NotoSansJP" color="#156f55">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r'<link href="\2" color="#1677b3">\1</link>', text)
    text = re.sub(r"&lt;(https?://[^&]+)&gt;", r'<link href="\1" color="#1677b3">\1</link>', text)
    return text


def make_styles(font: str, serif: str):
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TitleJP", parent=base["Title"], fontName=serif, fontSize=25,
            leading=32, textColor=colors.HexColor("#101820"), alignment=TA_CENTER,
            spaceAfter=10 * mm,
        ),
        "h1": ParagraphStyle(
            "H1JP", parent=base["Heading1"], fontName=font, fontSize=16,
            leading=23, textColor=colors.HexColor("#0f6f55"), spaceBefore=6 * mm,
            spaceAfter=3 * mm, keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "H2JP", parent=base["Heading2"], fontName=font, fontSize=12.5,
            leading=18, textColor=colors.HexColor("#263238"), spaceBefore=4 * mm,
            spaceAfter=2 * mm, keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "BodyJP", parent=base["BodyText"], fontName=font, fontSize=9.4,
            leading=15.2, textColor=colors.HexColor("#263238"), spaceAfter=2.2 * mm,
            wordWrap="CJK",
        ),
        "small": ParagraphStyle(
            "SmallJP", parent=base["BodyText"], fontName=font, fontSize=8.2,
            leading=12.3, textColor=colors.HexColor("#455a64"), wordWrap="CJK",
        ),
        "callout": ParagraphStyle(
            "CalloutJP", parent=base["BodyText"], fontName=font, fontSize=8.8,
            leading=14, textColor=colors.HexColor("#183b32"), backColor=colors.HexColor("#eaf6f1"),
            borderColor=colors.HexColor("#74b89f"), borderWidth=0.8, borderPadding=7,
            spaceAfter=3 * mm, wordWrap="CJK",
        ),
        "caption": ParagraphStyle(
            "CaptionJP", parent=base["BodyText"], fontName=font, fontSize=7.5,
            leading=11, textColor=colors.HexColor("#607d8b"), alignment=TA_CENTER,
            spaceBefore=1 * mm, spaceAfter=4 * mm,
        ),
    }


def page_decor(canvas, doc):
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(colors.HexColor("#d9e3df"))
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, height - 14 * mm, width - 18 * mm, height - 14 * mm)
    canvas.setFont("NotoSansJP", 7.2)
    canvas.setFillColor(colors.HexColor("#607d8b"))
    canvas.drawString(18 * mm, 8 * mm, "競馬AI Pro 操作手順書 - Limited Production / Observation")
    canvas.drawRightString(width - 18 * mm, 8 * mm, f"{doc.page}")
    canvas.restoreState()


def parse_table(lines: list[str], styles) -> Table:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append([Paragraph(inline_markup(cell), styles["small"]) for cell in cells])
    table = Table(rows, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f6f55")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "NotoSansJP"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d8d2")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f8f6")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def build_story(source: Path, styles) -> list:
    lines = source.read_text(encoding="utf-8").splitlines()
    story = []
    bullet_items: list[str] = []
    number_items: list[str] = []

    def flush_lists():
        nonlocal bullet_items, number_items
        if bullet_items:
            items = [ListItem(Paragraph(inline_markup(x), styles["body"]), leftIndent=5) for x in bullet_items]
            story.append(ListFlowable(items, bulletType="bullet", leftIndent=14, bulletFontName="NotoSansJP", bulletFontSize=7))
            story.append(Spacer(1, 2 * mm))
            bullet_items = []
        if number_items:
            items = [ListItem(Paragraph(inline_markup(x), styles["body"]), leftIndent=5) for x in number_items]
            story.append(ListFlowable(items, bulletType="1", start="1", leftIndent=18, bulletFontName="NotoSansJP", bulletFontSize=8))
            story.append(Spacer(1, 2 * mm))
            number_items = []

    i = 0
    first_title = True
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line:
            flush_lists()
            i += 1
            continue
        if line.startswith("| "):
            flush_lists()
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            story.append(parse_table(table_lines, styles))
            story.append(Spacer(1, 4 * mm))
            continue
        image_match = re.fullmatch(r"!\[([^]]*)\]\(([^)]+)\)", line)
        if image_match:
            flush_lists()
            caption, rel = image_match.groups()
            path = source.parent / rel
            if not path.exists():
                raise FileNotFoundError(path)
            img = Image(str(path))
            max_w, max_h = 170 * mm, 118 * mm
            scale = min(max_w / img.imageWidth, max_h / img.imageHeight, 1.0)
            img.drawWidth = img.imageWidth * scale
            img.drawHeight = img.imageHeight * scale
            story.append(KeepTogether([img, Paragraph(inline_markup(caption), styles["caption"])]))
            i += 1
            continue
        if line.startswith("# "):
            flush_lists()
            if not first_title:
                story.append(PageBreak())
            story.append(Paragraph(inline_markup(line[2:]), styles["title"]))
            first_title = False
        elif line.startswith("## "):
            flush_lists()
            story.append(Paragraph(inline_markup(line[3:]), styles["h1"]))
        elif line.startswith("### "):
            flush_lists()
            story.append(Paragraph(inline_markup(line[4:]), styles["h2"]))
        elif line.startswith("> "):
            flush_lists()
            callout = line[2:].replace("  ", "<br/>")
            story.append(Paragraph(inline_markup(callout).replace("&lt;br/&gt;", "<br/>"), styles["callout"]))
        elif re.match(r"^- ", line):
            flush_lists() if number_items else None
            bullet_items.append(line[2:])
        elif re.match(r"^\d+\. ", line):
            flush_lists() if bullet_items else None
            number_items.append(re.sub(r"^\d+\. ", "", line))
        else:
            flush_lists()
            story.append(Paragraph(inline_markup(line), styles["body"]))
        i += 1
    flush_lists()
    return story


def main() -> None:
    font, serif = register_fonts()
    styles = make_styles(font, serif)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=20 * mm, bottomMargin=15 * mm,
        title="競馬AI Pro 操作手順書",
        author="keiba-ai-pro",
        subject="Limited Production / Observation user operation guide",
    )
    story = build_story(SOURCE, styles)
    doc.build(story, onFirstPage=page_decor, onLaterPages=page_decor)
    print(OUTPUT)


if __name__ == "__main__":
    main()
