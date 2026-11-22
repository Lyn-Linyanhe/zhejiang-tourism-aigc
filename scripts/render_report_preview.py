"""用 Word 更新域（目录/页码）并把报告导出为 PDF，再渲染成 PNG 供抽检。"""

from __future__ import annotations

import pathlib
import sys

import fitz
import win32com.client

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCX = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "报告10.8-修订-含图-v2.docx"
DOCX = DOCX if DOCX.is_absolute() else ROOT / DOCX
PDF = ROOT / "runtime" / "report-assets" / "preview.pdf"
PAGES = ROOT / "runtime" / "report-assets" / "pages"


def export() -> None:
    PDF.parent.mkdir(parents=True, exist_ok=True)
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        document = word.Documents.Open(str(DOCX), ReadOnly=False)
        for field in document.Fields:
            field.Update()
        for index in range(1, document.TablesOfContents.Count + 1):
            document.TablesOfContents(index).Update()
        document.Repaginate()
        document.Save()
        document.ExportAsFixedFormat(str(PDF), 17)  # 17 = wdExportFormatPDF
        document.Close(SaveChanges=0)
    finally:
        word.Quit()


def to_png() -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    for existing in PAGES.glob("page-*.png"):
        existing.unlink()
    with fitz.open(PDF) as pdf:
        print("pages:", pdf.page_count)
        for index, page in enumerate(pdf):
            pixmap = page.get_pixmap(dpi=105)
            pixmap.save(PAGES / f"page-{index + 1:02d}.png")


if __name__ == "__main__":
    export()
    to_png()
    sys.stdout.write("done\n")
