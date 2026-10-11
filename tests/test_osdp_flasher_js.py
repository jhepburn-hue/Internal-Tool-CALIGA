"""Runs the browser flasher's Node tests.

tests/js/osdp_reference.json holds reference OSDP frames recorded from the original Python
OSDP library (osdp_library, since removed). The flasher must match them byte for byte.
"""
import os
import shutil
import subprocess

import pytest

from conftest import ROOT


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js isn't installed.")
def test_browser_flasher_matches_reference_frames():
    result = subprocess.run(["node", os.path.join(ROOT, "tests", "js", "osdp-flasher.test.mjs"),
                             os.path.join(ROOT, "static", "js", "osdp-flasher.js"),
                             os.path.join(ROOT, "tests", "js", "osdp_reference.json")],
                            capture_output=True, text=True, timeout=120)
    failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
    assert result.returncode == 0 and not failures, "\n".join(failures) or result.stderr[-2000:]
