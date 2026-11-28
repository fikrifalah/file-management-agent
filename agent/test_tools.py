import shutil
import sqlite3
import unittest
from pathlib import Path

import main as agent_main
from docx import Document
from openpyxl import Workbook
from pypdf import PdfWriter
from tools import (
    AGENT_DIR,
    BASE_DIR,
    create_directory,
    create_file,
    delete_file,
    get_rag_engine,
    initialize_rag_index,
    list_directories,
    move_file,
    rag_search,
    read_file,
    replace_in_file,
    search_in_files,
)

SANDBOX = "_test_suite_sandbox"
SANDBOX_PATH = BASE_DIR / SANDBOX


def _create_sample_pdf_with_text(pdf_path: Path, text: str) -> None:
    """Write a minimal valid 1-page PDF containing uncompressedsearchable text."""
    stream_bytes = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n",
        f"4 0 obj << /Length {len(stream_bytes)} >> stream\n".encode("latin-1") + stream_bytes + b"\nendstream endobj\n",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1")
    )
    pdf_path.write_bytes(bytes(out))


class TestAgentSecurityAndTools(unittest.TestCase):
    def setUp(self):
        if SANDBOX_PATH.exists():
            shutil.rmtree(SANDBOX_PATH)

    def tearDown(self):
        if SANDBOX_PATH.exists():
            delete_file(SANDBOX, recursive=True)

    def test_01_security_traversal_and_protected_paths(self):
        blocked_targets = [
            "..",
            "../..",
            "agent",
            "agent/tools.py",
            "agent/.env",
            ".env",
            ".env.local",
            ".git/config",
            ".venv/pyvenv.cfg",
            "node_modules/pkg/index.js",
            "rag_index.db",
        ]
        for bad in blocked_targets:
            self.assertIn("Access denied", list_directories(bad), f"Failed on {bad}")
            self.assertIn("Access denied", read_file(bad), f"Failed on {bad}")
            self.assertIn("Access denied", create_file(bad, content="x"), f"Failed on {bad}")
            self.assertIn("Access denied", delete_file(bad, recursive=True), f"Failed on {bad}")

    def test_02_root_protection(self):
        self.assertIn("Error: Cannot delete", delete_file(".", recursive=True))
        self.assertIn("Error: Cannot move", move_file(".", f"{SANDBOX}/moved_root"))

    def test_03_hidden_from_listings_and_search(self):
        listing = list_directories(".")
        self.assertNotIn("agent/", listing)
        self.assertNotIn(".git/", listing)
        self.assertNotIn(".env", listing)

        search_res = search_in_files("GROQ_API_KEY", ".")
        self.assertNotIn("agent", search_res)

    def test_04_file_lifecycle_and_edits(self):
        res_dir = create_directory(f"{SANDBOX}/sub")
        self.assertIn("Created directory", res_dir)

        res_file = create_file(f"{SANDBOX}/hello", suffix="py", content="a = 1\nb = 2\nc = 3\n")
        self.assertIn("Created file", res_file)
        self.assertTrue((SANDBOX_PATH / "hello.py").is_file())

        res_dup = create_file(f"{SANDBOX}/hello.py", content="overwrite")
        self.assertIn("Error:", res_dup)

        content = read_file(f"{SANDBOX}/hello.py", start_line=2, end_line=3)
        self.assertIn("2: b = 2", content)
        self.assertIn("3: c = 3", content)
        self.assertNotIn("1: a = 1", content)

        found = search_in_files("b = 2", SANDBOX)
        self.assertIn("hello.py:2:", found)

        self.assertIn("Error:", replace_in_file(f"{SANDBOX}/hello.py", "", "x"))
        self.assertIn("Error:", replace_in_file(f"{SANDBOX}/hello.py", "=", "=="))
        self.assertIn("Updated", replace_in_file(f"{SANDBOX}/hello.py", "b = 2", "b = 42"))
        self.assertIn("2: b = 42", read_file(f"{SANDBOX}/hello.py"))

        bin_path = SANDBOX_PATH / "bin.dat"
        bin_path.write_bytes(b"\x00\x01\x02binary")
        self.assertIn("binary file", read_file(f"{SANDBOX}/bin.dat"))

        self.assertIn("Moved", move_file(f"{SANDBOX}/hello.py", f"{SANDBOX}/sub"))
        self.assertTrue((SANDBOX_PATH / "sub" / "hello.py").is_file())

        self.assertIn("Error:", delete_file(SANDBOX, recursive=False))
        self.assertIn("Deleted directory", delete_file(SANDBOX, recursive=True))
        self.assertFalse(SANDBOX_PATH.exists())

    def test_05_agent_construction(self):
        instructions = agent_main.load_instructions()
        self.assertTrue(instructions and "rag_search" in instructions)
        agent = agent_main.build_agent("openai/gpt-oss-120b", "dummy_key_for_init_test")
        self.assertIn("rag_search", agent.tools)

    def test_06_rag_parsers_sqlite_vec_and_auto_embedding(self):
        SANDBOX_PATH.mkdir(parents=True, exist_ok=True)

        # 1. Create real PDF, DOCX, and XLSX documents on disk
        pdf_file = SANDBOX_PATH / "handbook_policy.pdf"
        _create_sample_pdf_with_text(pdf_file, "Project Hyperion launch code is ORBIT-9921 on Mars.")

        docx_file = SANDBOX_PATH / "onboarding_guide.docx"
        doc = Document()
        doc.add_paragraph("Employees receive an annual learning stipend of 2500 USD for courses.")
        doc.save(str(docx_file))

        xlsx_file = SANDBOX_PATH / "q4_budget.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "Revenue"
        ws.append(["Department", "Allocation", "Lead"])
        ws.append(["Quantum Robotics", "840000", "Dr. Elena Vance"])
        wb.save(str(xlsx_file))

        # 2. Index the entire workspace directory at runtime
        stats = initialize_rag_index()
        self.assertGreaterEqual(stats["indexed_files"], 3)

        # 3. Verify semantic search across PDF, DOCX, and XLSX using 1-arg rag_search(query)
        res_pdf = rag_search("What is the Project Hyperion launch code?")
        self.assertIn("handbook_policy.pdf", res_pdf)
        self.assertIn("ORBIT-9921", res_pdf)

        res_docx = rag_search("How much is the employee annual learning stipend?")
        self.assertIn("onboarding_guide.docx", res_docx)
        self.assertIn("2500", res_docx)

        res_xlsx = rag_search("Who leads the Quantum Robotics budget allocation?")
        self.assertIn("q4_budget.xlsx", res_xlsx)
        self.assertIn("Elena Vance", res_xlsx)

        # 4. Verify agent-created files are automatically embedded into RAG without re-running directory scan
        create_file(
            f"{SANDBOX}/secret_recipe.md",
            content="The secret ingredient for the midnight espresso glaze is cardamom pods.",
            overwrite=True,
        )
        res_auto = rag_search("midnight espresso glaze secret ingredient")
        self.assertIn("secret_recipe.md", res_auto)
        self.assertIn("cardamom", res_auto)

        # 5. Verify SQLite chunks table explicitly stores file_name on every chunk
        db_path = AGENT_DIR / "rag_index.db"
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT file_name, content FROM chunks WHERE file_name = ?",
                ("secret_recipe.md",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "secret_recipe.md")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
