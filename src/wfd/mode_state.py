"""Persist sink-advertised Miracast modes for optional external UIs.

When FLUXCAST_WFD_MODE_STATE points at a file path, write JSON after RTSP
mode negotiation (current mode, supported CEA/VESA list, peer identity).
Off by default — no file, no I/O.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from .config import WFDCEAMode, WFDVideoFormat
from .modes import WFD_CEA_MODES, WFD_VESA_MODES

_INTERLACED = re.compile(r"i\d+$")


def _mode_state_path() -> str:
    return os.environ.get("FLUXCAST_WFD_MODE_STATE", "").strip()


def _is_interlaced(mode: WFDCEAMode) -> bool:
    if getattr(mode, "interlaced", False):
        return True
    return bool(_INTERLACED.search(mode.name or ""))


def supported_modes(sink_format: Optional[WFDVideoFormat]) -> list[dict]:
    """FluxCast-known modes the sink's CEA/VESA masks allow.

    H.264 level bitmap is not a hard filter — Realtek-class sinks often
    omit higher level bits while still setting the CEA mode bit.
    """
    if sink_format is None:
        return []
    out: list[dict] = []
    for _bit, mode in {**WFD_CEA_MODES, **WFD_VESA_MODES}.items():
        if mode.table == "vesa":
            if not (sink_format.vesa_mask & mode.bit):
                continue
        elif not (sink_format.cea_mask & mode.bit):
            continue
        out.append(_mode_dict(mode))
    out.sort(
        key=lambda m: (
            1 if m.get("interlaced") else 0,
            m["height"],
            m["width"],
            m["fps"],
        )
    )
    return out


def _mode_dict(mode: WFDCEAMode) -> dict:
    interlaced = _is_interlaced(mode)
    return {
        "id": mode.name,
        "label": f"{mode.width}x{mode.height}{'i' if interlaced else 'p'}@{mode.fps}",
        "width": mode.width,
        "height": mode.height,
        "fps": mode.fps,
        "resolution": f"{mode.width}x{mode.height}",
        "interlaced": interlaced,
        "negotiable": not interlaced,
    }


def write_mode_state(
    *,
    sink_format: Optional[WFDVideoFormat],
    current: Optional[WFDCEAMode],
    peer: str = "",
    peer_name: str = "",
) -> None:
    path = _mode_state_path()
    if not path:
        return
    payload = {
        "peer": peer,
        "peerName": peer_name,
        "current": current.name if current else "",
        "supported": supported_modes(sink_format),
        "ceaMask": f"0x{sink_format.cea_mask:08x}" if sink_format else "",
        "vesaMask": f"0x{sink_format.vesa_mask:08x}" if sink_format else "",
    }
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
            fh.write("\n")
    except OSError as exc:
        print(f"[FluxCast WFD] Could not write mode state: {exc}")
