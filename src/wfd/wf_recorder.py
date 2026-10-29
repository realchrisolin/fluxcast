"""Discovery and validation for the wlroots screen recorder."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

# AppImage wrappers exit 127 when the host wf-recorder binary is missing.
_WRAPPER_MISSING_BINARY = 127

# Cache ICC capability keyed by resolved path (help parse is a few ms).
_icc_cache: dict[str, bool] = {}


def _usable_recorder(path: str) -> bool:
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=3.0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    # Reject only the AppImage "command not found" exit; other non-zero codes
    # (e.g. older builds without --version) must not disable a working binary.
    return result.returncode != _WRAPPER_MISSING_BINARY


def wf_recorder_supports_icc(path: str) -> bool:
    """True when ``path`` looks like an ext-image-copy-capture (PR #347) build.

    Detection: ``--help`` advertises ``--toplevel``, or ``--version`` mentions
    ``ext-copy-capture``. Stock 0.6.0 has neither. Not used to auto-pick PATH.
    """
    if not path:
        return False
    cached = _icc_cache.get(path)
    if cached is not None:
        return cached
    blob = ""
    try:
        ver = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=3.0
        )
        blob += (ver.stdout or "") + (ver.stderr or "")
        help_ = subprocess.run(
            [path, "--help"], capture_output=True, text=True, timeout=3.0
        )
        blob += (help_.stdout or "") + (help_.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        _icc_cache[path] = False
        return False
    lower = blob.lower()
    ok = ("--toplevel" in lower) or ("ext-copy-capture" in lower)
    _icc_cache[path] = ok
    return ok


def icc_opt_in() -> bool:
    """User asked for ICC via BIN and/or PROTO. Never inferred from PATH."""
    proto = (os.environ.get("FLUXCAST_WFD_WF_RECORDER_PROTO") or "").strip().lower()
    override = (os.environ.get("FLUXCAST_WFD_WF_RECORDER_BIN") or "").strip()
    return proto == "icc" or bool(override)


def find_wf_recorder() -> Optional[str]:
    """Return a usable wf-recorder path, or None if a wrapper cannot run it.

    Selection (first match wins):

    1. ``FLUXCAST_WFD_WF_RECORDER_BIN`` if set and usable
    2. ``PATH`` ``wf-recorder`` if usable

    When ``FLUXCAST_WFD_WF_RECORDER_PROTO=icc``, the chosen binary must support
    ext-image-copy-capture; otherwise return None (fail closed). Unset PROTO
    accepts any usable binary — ICC is opt-in, never auto-probed from PATH.
    """
    proto = (os.environ.get("FLUXCAST_WFD_WF_RECORDER_PROTO") or "").strip().lower()
    override = (os.environ.get("FLUXCAST_WFD_WF_RECORDER_BIN") or "").strip()
    candidates: list[str] = []
    if override:
        candidates.append(override)
    which = shutil.which("wf-recorder")
    if which and which not in candidates:
        candidates.append(which)

    chosen: Optional[str] = None
    for path in candidates:
        if _usable_recorder(path):
            chosen = path
            break
    if chosen is None:
        return None

    if proto == "icc" and not wf_recorder_supports_icc(chosen):
        return None
    return chosen


def wf_capture_rate_args(
    path: str,
    fps: int,
    *,
    vaapi_dmabuf: bool = False,
) -> list[str]:
    """``-r`` for raw/pipe capture. DMA-BUF encode omits it.

    ICC (opt-in BIN/PROTO) may override with ``FLUXCAST_WFD_ICC_CAPTURE_FPS``.
    Stock wlr-screencopy raw still gets ``-r`` at encode fps.
    """
    if vaapi_dmabuf:
        return []
    rate = "30"
    try:
        rate = str(int(fps))
    except (TypeError, ValueError):
        pass
    if icc_opt_in() and wf_recorder_supports_icc(path):
        override = (os.environ.get("FLUXCAST_WFD_ICC_CAPTURE_FPS") or "").strip()
        return ["-r", override or rate]
    return ["-r", rate]
