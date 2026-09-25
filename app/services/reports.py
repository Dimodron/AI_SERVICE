import csv
import io
import re
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from schemas.reports import ReportCreate, ReportResponse
from starlette.concurrency import run_in_threadpool

MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_XML = "http://www.w3.org/XML/1998/namespace"
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _text(value) -> str:
    return _ILLEGAL_XML.sub("", "" if value is None else str(value))


def _csv_cell(value):
    # CSV consumers can otherwise interpret user/model text as formulas.
    if isinstance(value, str):
        value = _text(value)
        if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
            return "'" + value
    return value


def _word_paragraph(parent, text):
    paragraph = ET.SubElement(parent, f"{{{_WORD}}}p")
    for index, line in enumerate(_text(text).split("\n")):
        run = ET.SubElement(paragraph, f"{{{_WORD}}}r")
        if index:
            ET.SubElement(run, f"{{{_WORD}}}br")
        node = ET.SubElement(run, f"{{{_WORD}}}t", {f"{{{_XML}}}space": "preserve"})
        node.text = line


def render_report(report: ReportCreate) -> bytes:
    if report.format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        if report.text:
            writer.writerow([_csv_cell(report.title)])
            writer.writerow([_csv_cell(report.text)])
            writer.writerow([])
        if report.columns:
            writer.writerow([_csv_cell(value) for value in report.columns])
            writer.writerows([_csv_cell(value) for value in row] for row in report.rows)
        return stream.getvalue().encode("utf-8-sig")

    stream = io.BytesIO()
    if report.format == "xlsx":
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Отчёт")

        def append(values):
            cells = []
            for value in values:
                # Long integers must remain exact in spreadsheet applications.
                if isinstance(value, int) and not isinstance(value, bool) and abs(value) >= 10**15:
                    value = str(value)
                cell = WriteOnlyCell(sheet, value=_text(value) if isinstance(value, str) else value)
                if isinstance(value, str):
                    cell.data_type = "s"
                cells.append(cell)
            sheet.append(cells)

        append([report.title])
        if report.text:
            append([report.text])
        if report.columns:
            append(report.columns)
            for row in report.rows:
                append(row)
        workbook.save(stream)
        workbook.close()
    else:
        # A minimal DOCX package, using only stdlib XML/ZIP; no external assets.
        document = ET.Element(f"{{{_WORD}}}document")
        body = ET.SubElement(document, f"{{{_WORD}}}body")
        _word_paragraph(body, report.title)
        if report.text:
            _word_paragraph(body, report.text)
        if report.columns:
            table = ET.SubElement(body, f"{{{_WORD}}}tbl")
            grid = ET.SubElement(table, f"{{{_WORD}}}tblGrid")
            for _ in report.columns:
                ET.SubElement(grid, f"{{{_WORD}}}gridCol", {f"{{{_WORD}}}w": str(9000 // len(report.columns))})
            for values in [report.columns, *report.rows]:
                row = ET.SubElement(table, f"{{{_WORD}}}tr")
                for value in values:
                    cell = ET.SubElement(row, f"{{{_WORD}}}tc")
                    _word_paragraph(cell, value)
        ET.SubElement(body, f"{{{_WORD}}}sectPr")
        with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            archive.writestr("word/document.xml", ET.tostring(document, encoding="utf-8", xml_declaration=True))
    return stream.getvalue()


async def create_report(connection, report: ReportCreate) -> ReportResponse:
    content = await run_in_threadpool(render_report, report)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "Готовый отчёт превышает 5 МБ")
    name = re.sub(r"[^\w .-]", "_", report.title, flags=re.UNICODE).strip(" .")[:120] or "report"
    filename = f"{name}.{report.format}"
    file_id = uuid4()
    extracted = "\n".join([report.title, report.text, "\t".join(report.columns),
                            *("\t".join(_text(cell) for cell in row) for row in report.rows)])
    cursor = await connection.execute(
        "INSERT INTO files (id, filename, media_type, size_bytes, extracted_text, content) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "RETURNING id AS file_id, filename, media_type, size_bytes, created_at",
        (file_id, filename, MEDIA_TYPES[report.format], len(content), extracted, content),
    )
    return ReportResponse(**await cursor.fetchone(), download_url=f"/api/files/{file_id}/download")
