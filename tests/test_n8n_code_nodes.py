"""Behaviour of the Telegram Code nodes (tests/n8n_code_nodes.js): needs node, skipped without it."""
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_telegram_code_nodes():
    out = subprocess.run([NODE, str(Path(__file__).with_name("n8n_code_nodes.js"))], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
