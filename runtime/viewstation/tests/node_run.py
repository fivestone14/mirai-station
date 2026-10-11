"""Run a script in node from a file, never as one `node -e` argument: Linux caps a single
argument at 128 KB and the page code the tests lift out has outgrown it. macOS has no such
cap, so `-e` passed here and failed only in CI with "Argument list too long"."""
import os
import subprocess
import tempfile


def run_node(node, script, **kwargs):
    """subprocess.run of ``node`` on ``script``, with the same keyword arguments."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
    try:
        return subprocess.run([node, f.name], **kwargs)
    finally:
        os.unlink(f.name)
