"""Test package. Points every XDG dir at a throwaway location before any test runs, so
no test can write to the real ~/.config, ~/.cache or ~/.local/share (scanning logs, for
example, persists history). Individual tests still use their own temp dirs."""

import atexit
import os
import shutil
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="ai-usage-tests-")
atexit.register(shutil.rmtree, _SANDBOX, ignore_errors=True)
for _var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
    os.environ[_var] = os.path.join(_SANDBOX, _var.lower())
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(_SANDBOX, "claude")
