"""Opt-in ffmpeg h264_vaapi for the wf-recorder pipe (issue #19).

Portal GPU encode is ``--wfd-encoder va`` (GStreamer ``vah264enc``). This
module is the remaining wf-recorder path: rawvideo from wf-recorder, then
``h264_vaapi`` in ffmpeg. Default stays libx264. No DMA-BUF, no ICC.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .config import WFDMediaConfig
from .encoding import _letterbox_vf, _vbv_bufsize, _x264_threads
from .modes import _h264_level_for_mode

WF_RECORDER_VAAPI = "vaapi"
_ENCODERS = ("x264", "va", WF_RECORDER_VAAPI)
_UNSET = object()


def resolve_encoder(cli: str | None, env: str | None = None) -> str:
    """CLI wins when it is not the argparse default; env fills the default.

    ``va`` remains the portal GStreamer encoder. ``vaapi`` is ffmpeg
    ``h264_vaapi`` on the wf-recorder pipe.
    """
    env_raw = (env if env is not None else os.environ.get("FLUXCAST_WFD_ENCODER") or "")
    env_val = env_raw.strip().lower()
    cli_val = (cli or "x264").strip().lower() or "x264"
    if env_val in _ENCODERS and cli_val == "x264":
        return env_val
    if cli_val in _ENCODERS:
        return cli_val
    return "x264"


def vaapi_render_device(root: str | Path = "/dev/dri") -> str | None:
    path = Path(root) / "renderD128"
    return str(path) if path.exists() else None


def ffmpeg_has_h264_vaapi(ffmpeg: str | None = None) -> bool:
    bin_path = ffmpeg or shutil.which("ffmpeg")
    if not bin_path:
        return False
    try:
        result = subprocess.run(
            [bin_path, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = (result.stdout or "") + (result.stderr or "")
    return "h264_vaapi" in text


def want_wf_recorder_vaapi(encoder: str) -> bool:
    return encoder == WF_RECORDER_VAAPI


def vaapi_ffmpeg_global_args(device: str) -> list[str]:
    return [
        "-init_hw_device", f"vaapi=va:{device}",
        "-filter_hw_device", "va",
    ]


def _vaapi_profile(config: WFDMediaConfig) -> str:
    profile = (config.h264_profile or "baseline").strip().lower()
    if profile in ("baseline", "constrained_baseline"):
        return "constrained_baseline"
    return profile


def ffmpeg_h264_pipe_args(
    config: WFDMediaConfig,
    *,
    src_res: str,
    out_res: str,
    bitrate: str,
    gop: int,
    parsed_out_height: int,
    vaapi_device: str | None | object = _UNSET,
    has_h264_vaapi: bool | None = None,
) -> tuple[list[str], str, bool]:
    """``-vf`` plus H.264 encoder args after stream maps.

    Returns ``(args, label, used_vaapi)``. Falls back to libx264 when VAAPI
    was requested but the device or encoder is missing.
    """
    use_vaapi = want_wf_recorder_vaapi(config.encoder)
    device = vaapi_render_device() if vaapi_device is _UNSET else vaapi_device
    if use_vaapi:
        if has_h264_vaapi is None:
            has_h264_vaapi = ffmpeg_has_h264_vaapi()
        if not device or not has_h264_vaapi:
            use_vaapi = False
            print(
                "[FluxCast WFD Media] --wfd-encoder vaapi requested but "
                "h264_vaapi / render node is missing; falling back to libx264",
                flush=True,
            )

    if use_vaapi:
        if out_res == src_res:
            vf = "format=nv12,hwupload"
        else:
            vf = _letterbox_vf(out_res).replace("format=yuv420p", "format=nv12,hwupload")
        args = [
            "-vf", vf,
            "-c:v", "h264_vaapi",
            "-profile:v", _vaapi_profile(config),
            "-level:v", _h264_level_for_mode(config),
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-bf", "0",
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-bufsize", _vbv_bufsize(bitrate, config),
        ]
        return args, "h264_vaapi", True

    if out_res == src_res:
        vf = "format=yuv420p"
    else:
        vf = _letterbox_vf(out_res)
    args = [
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "ultrafast" if parsed_out_height > 1080 else "veryfast",
        "-tune", "zerolatency",
        "-threads", str(_x264_threads()),
        "-profile:v", config.h264_profile,
        "-level:v", _h264_level_for_mode(config),
        "-pix_fmt", "yuv420p",
        "-r", str(config.fps),
        "-g", str(gop),
        "-keyint_min", str(gop),
        "-sc_threshold", "0",
        "-bf", "0",
        "-b:v", bitrate,
        "-maxrate", bitrate,
        "-bufsize", _vbv_bufsize(bitrate, config),
        "-x264-params", "repeat-headers=1:aud=1",
    ]
    return args, "libx264", False
