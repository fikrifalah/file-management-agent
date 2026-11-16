import shutil
import unittest
from pathlib import Path

import main as agent_main
from tools import (
    BASE_DIR,
    create_directory,
    create_file,
    delete_file,
    list_directories,
    move_file,
    read_file,
    replace_in_file,
    search_in_files,
)

SANDBOX = "_test_suite_sandbox"
SANDBOX_PATH = BASE_DIR / SANDBOX


class TestAgentSecurityAndTools(unittest.TestCase):
    def setUp(self):
        if SANDBOX_PATH.exists():
            shutil.rmtree(SANDBOX_PATH)

    def tearDown(self):
        if SANDBOX_PATH.exists():
            shutil.rmtree(SANDBOX_PATH)

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
        # Create directory & file with suffix normalization
        res_dir = create_directory(f"{SANDBOX}/sub")
        self.assertIn("Created directory", res_dir)

        res_file = create_file(f"{SANDBOX}/hello", suffix="py", content="a = 1\nb = 2\nc = 3\n")
        self.assertIn("Created file", res_file)
        self.assertTrue((SANDBOX_PATH / "hello.py").is_file())

        # Prevent accidental overwrite when overwrite=False
        res_dup = create_file(f"{SANDBOX}/hello.py", content="overwrite")
        self.assertIn("Error:", res_dup)

        # Read file with line numbers and slicing
        content = read_file(f"{SANDBOX}/hello.py", start_line=2, end_line=3)
        self.assertIn("2: b = 2", content)
        self.assertIn("3: c = 3", content)
        self.assertNotIn("1: a = 1", content)

        # Search in files
        found = search_in_files("b = 2", SANDBOX)
        self.assertIn("hello.py:2:", found)

        # Replace in file: empty old_text blocked, duplicate old_text blocked, unique succeeds
        self.assertIn("Error:", replace_in_file(f"{SANDBOX}/hello.py", "", "x"))
        self.assertIn("Error:", replace_in_file(f"{SANDBOX}/hello.py", "=", "=="))  # matches 3 times
        self.assertIn("Updated", replace_in_file(f"{SANDBOX}/hello.py", "b = 2", "b = 42"))
        self.assertIn("2: b = 42", read_file(f"{SANDBOX}/hello.py"))

        # Binary file rejection
        bin_path = SANDBOX_PATH / "bin.dat"
        bin_path.write_bytes(b"\x00\x01\x02binary")
        self.assertIn("binary file", read_file(f"{SANDBOX}/bin.dat"))

        # Move file into subdirectory
        self.assertIn("Moved", move_file(f"{SANDBOX}/hello.py", f"{SANDBOX}/sub"))
        self.assertTrue((SANDBOX_PATH / "sub" / "hello.py").is_file())

        # Non-empty directory delete requires recursive=True
        self.assertIn("Error:", delete_file(SANDBOX, recursive=False))
        self.assertIn("Deleted directory", delete_file(SANDBOX, recursive=True))
        self.assertFalse(SANDBOX_PATH.exists())

    def test_05_agent_construction(self):
        instructions = agent_main.load_instructions()
        self.assertTrue(instructions and "project root" in instructions)
        agent = agent_main.build_agent("openai/gpt-oss-120b", "dummy_key_for_init_test")
        self.assertIsNotNone(agent)


if __name__ == "__main__":
    unittest.main(verbosity=2)
