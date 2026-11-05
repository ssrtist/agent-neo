"""GUI JS helpers under node (plan v0.5 D1 path rendering). Skipped when node is not installed."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from tests.support import REPO


@unittest.skipUnless(shutil.which("node"), "node not installed")
class GuiJsTests(unittest.TestCase):
    def test_raw_windows_paths_survive_markdown(self) -> None:
        r = subprocess.run(["node", str(REPO / "tests" / "js" / "paths_test.mjs")], cwd=REPO,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL PASS", r.stdout)


if __name__ == "__main__":
    unittest.main()
