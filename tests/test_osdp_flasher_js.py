"""Runs the browser flasher's Node tests against reference frames from osdp_library."""
import os
import shutil
import subprocess
import sys

import pytest

from conftest import ROOT


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js isn't installed.")
def test_browser_flasher_matches_osdp_library(tmp_path):
    reference = tmp_path / "osdp_reference.json"
    subprocess.run([sys.executable, os.path.join(ROOT, "tests", "support", "osdp_reference.py"), ROOT, str(reference)],
                   check=True, capture_output=True, timeout=60)
    result = subprocess.run(["node", os.path.join(ROOT, "tests", "js", "osdp-flasher.test.mjs"),
                             os.path.join(ROOT, "static", "js", "osdp-flasher.js"), str(reference)],
                            capture_output=True, text=True, timeout=120)
    failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
    assert result.returncode == 0 and not failures, "\n".join(failures) or result.stderr[-2000:]
