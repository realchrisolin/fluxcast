"""Choose WFD audio from the sink's advertised codecs.

Default stays AAC when the sink lists AAC (Samsung). LPCM-only dongles
(hotyeah / many Realtek sticks) advertise LPCM without AAC — those get
WFD LPCM instead of the old video-only fallback. Microsoft adapters still
prefer LPCM by name.

``FLUXCAST_WFD_PREFER_LPCM=1`` is true LPCM-first when both codecs are
listed. ``FLUXCAST_WFD_FORCE_AAC=1`` keeps AAC.
"""

from __future__ import annotations

import os

from .constants import WFD_AUDIO_AAC, WFD_AUDIO_LPCM_48K

AUDIO_NONE = "none"
AUDIO_AAC = "aac"
AUDIO_LPCM = "lpcm"
_TRUE = ("1", "true", "yes", "on")


def env_flag(name: str, env: dict[str, str] | None = None) -> bool:
    src = os.environ if env is None else env
    return (src.get(name) or "").strip().lower() in _TRUE


def choose_wfd_audio(
    advertised: str,
    *,
    peer_name: str = "",
    no_audio: bool = False,
    force_aac: bool | None = None,
    prefer_lpcm: bool | None = None,
) -> str:
    """Return AUDIO_NONE, AUDIO_AAC, or AUDIO_LPCM."""
    if no_audio:
        return AUDIO_NONE
    if force_aac is None:
        force_aac = env_flag("FLUXCAST_WFD_FORCE_AAC")
    if prefer_lpcm is None:
        prefer_lpcm = env_flag("FLUXCAST_WFD_PREFER_LPCM")
    caps = (advertised or "").upper()
    has_aac = "AAC" in caps
    has_lpcm = "LPCM" in caps
    microsoft = "microsoft" in (peer_name or "").lower()
    if force_aac:
        return AUDIO_AAC
    if microsoft:
        return AUDIO_LPCM
    if has_lpcm and (prefer_lpcm or not has_aac):
        return AUDIO_LPCM
    if has_aac:
        return AUDIO_AAC
    if advertised.strip() and not has_aac and not has_lpcm:
        return AUDIO_NONE
    return AUDIO_AAC


def rtsp_audio_line(choice: str) -> str:
    if choice == AUDIO_NONE:
        return "none"
    if choice == AUDIO_LPCM:
        return WFD_AUDIO_LPCM_48K
    return WFD_AUDIO_AAC
