from pathlib import Path

MAX_PARSE_BYTES = 20 * 1024 * 1024  # 20 MB cap for documents (PDF/DOCX/XLSX)
MAX_TEXT_BYTES = 5 * 1024 * 1024    # 5 MB cap for plain text/code files

DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xlsm"}

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".sql",
    ".sh", ".bash", ".bat", ".ps1", ".c", ".cpp", ".h", ".hpp", ".java",
    ".go", ".rs", ".rb", ".php", ".cs", ".swift", ".kt",
}


def is_supported_rag_file(path: Path) -> bool:
    ext = path.suffix.lower()
    return ext in DOCUMENT_EXTENSIONS or ext in TEXT_EXTENSIONS


def parse_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        txt = (page.extract_text() or "").strip()
        if txt:
            pages.append(f"[Page {idx}]\n{txt}")
    return "\n\n".join(pages)


def parse_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts: list[str] = []

    for para in doc.paragraphs:
        txt = para.text.strip()
        if txt:
            parts.append(txt)

    for t_idx, table in enumerate(doc.tables, start=1):
        rows_text: list[str] = []
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            if any(cells):
                rows_text.append(" | ".join(cells))
        if rows_text:
            parts.append(f"[Table {t_idx}]\n" + "\n".join(rows_text))

    return "\n\n".join(parts)


def parse_xlsx(path: Path) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    sheets_out: list[str] = []
    try:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows_out: list[str] = []
            for row in ws.iter_rows(values_only=True):
                if not row or all(cell is None or str(cell).strip() == "" for cell in row):
                    continue
                formatted = " | ".join("" if c is None else str(c).strip() for c in row)
                rows_out.append(formatted)
            if rows_out:
                sheets_out.append(f"[Sheet: {sheet_name}]\n" + "\n".join(rows_out))
    finally:
        wb.close()

    return "\n\n".join(sheets_out)


def parse_plain_text(path: Path) -> str:
    if path.stat().st_size > MAX_TEXT_BYTES:
        return ""
    with path.open("rb") as f:
        sample = f.read(1024)
    if b"\x00" in sample:
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def extract_text_from_file(path: Path) -> str:
    """Extract searchable plain text from PDF, DOCX, XLSX, or text/code files."""
    if not path.is_file():
        return ""

    size = path.stat().st_size
    if size == 0 or size > MAX_PARSE_BYTES:
        return ""

    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return parse_pdf(path)
        if ext == ".docx":
            return parse_docx(path)
        if ext in {".xlsx", ".xlsm"}:
            return parse_xlsx(path)
        if ext in TEXT_EXTENSIONS:
            return parse_plain_text(path)
    except Exception:
        return ""
    return ""


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
    """Split extracted text into overlapping chunks preserving line/paragraph boundaries."""
    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    length = len(cleaned)

    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            # Prefer breaking at double-newline, single-newline, or space
            for sep in ("\n\n", "\n", ". ", " "):
                split_pos = cleaned.rfind(sep, start + chunk_size // 2, end)
                if split_pos != -1:
                    end = split_pos + len(sep)
                    break

        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)

        if end >= length:
            break
        start = max(end - overlap, start + 1)

    return chunks
