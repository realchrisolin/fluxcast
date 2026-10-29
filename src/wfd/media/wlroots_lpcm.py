"""wf-recorder → existing WFDLPCMMuxer via GStreamer appsink.

Portal already feeds Gst appsink into ``drivers.wfd_lpcm_mux.WFDLPCMMuxer``
for Microsoft adapters. This module does the same on the wf-recorder path:
stock libx264 annex-B (no DMA-BUF) plus pulsesrc PCM. Default remains
ffmpeg AAC MPEG-TS/RTP.
"""

from __future__ import annotations

import os
import subprocess
import time

from ..config import WFDMediaConfig, WFDNotReady
from ..encoding import (
    _bitrate_to_kbits,
    _calculate_gop,
    _effective_kbits,
    _kbits_to_bitrate_text,
    _letterbox_vf,
    _parse_resolution,
    _vbv_bufsize,
    _x264_threads,
)
from ..env import _detect_audio_monitor
from ..modes import _h264_level_for_mode
from ..net import _ffmpeg_sender_args

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def want_wf_recorder_lpcm(config: WFDMediaConfig, env: str | None = None) -> bool:
    """True for --wfd-wf-recorder-lpcm, FLUXCAST_WFD_WF_RECORDER_LPCM=1, or Microsoft.

    Off when audio is disabled. Default (no flag/env, non-Microsoft) is False so
    Samsung/AAC sessions stay on ffmpeg mpegts.
    """
    if config.no_audio:
        return False
    if getattr(config, "wf_recorder_lpcm", False):
        return True
    raw = (env if env is not None else os.environ.get("FLUXCAST_WFD_WF_RECORDER_LPCM") or "")
    flag = raw.strip().lower()
    if flag in _TRUE:
        return True
    if flag in _FALSE:
        return False
    return "microsoft" in (config.peer_name or "").lower()


def gst_video_pipeline(read_fd: int) -> str:
    return (
        f"fdsrc fd={int(read_fd)} do-timestamp=true ! "
        "h264parse config-interval=-1 ! "
        "video/x-h264,stream-format=byte-stream,alignment=au ! "
        "appsink name=sink sync=false"
    )


def gst_audio_pipeline(pulse_device: str) -> str:
    device = (pulse_device or "").replace("\\", "\\\\").replace("'", "\\'")
    return (
        f"pulsesrc device='{device}' do-timestamp=true ! "
        "audioconvert ! audioresample ! "
        "audio/x-raw,format=S16BE,rate=48000,channels=2,layout=interleaved ! "
        "appsink name=sink sync=false"
    )


def ffmpeg_libx264_annexb_args(
    config: WFDMediaConfig,
    *,
    src_res: str,
    out_res: str,
    bitrate: str,
    gop: int,
    parsed_out_height: int,
) -> list[str]:
    """libx264 annex-B on stdout. No audio, no MPEG-TS/RTP."""
    if out_res == src_res:
        vf = "format=yuv420p"
    else:
        vf = _letterbox_vf(out_res)
    return [
        "-map", "0:v:0",
        "-an",
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
        "-f", "h264",
        "pipe:1",
    ]


def start_wf_recorder_lpcm(media, wf_recorder: str, monitor) -> None:
    """Start wf-recorder + libx264 annex-B into WFDLPCMMuxer."""
    try:
        from drivers.wfd_lpcm_mux import WFDLPCMMuxer
    except ImportError as exc:
        raise WFDNotReady(f"WFDLPCMMuxer unavailable: {exc}") from exc

    src_res = f"{monitor.width}x{monitor.height}"
    out_res = media.config.output_resolution or src_res
    audio_monitor = media.config.audio_device or _detect_audio_monitor()
    if not audio_monitor:
        raise WFDNotReady("wf-recorder LPCM path needs a Pulse/PipeWire monitor source.")
    gop = _calculate_gop(media.config)
    parsed_out = _parse_resolution(out_res) or (monitor.width, monitor.height)
    requested_kbits = _bitrate_to_kbits(media.config.bitrate)
    effective_kbits = _effective_kbits(
        media.config, requested_kbits, parsed_out[0], parsed_out[1]
    )
    effective_bitrate = _kbits_to_bitrate_text(effective_kbits)

    r_fd, w_fd = os.pipe()
    os.set_inheritable(w_fd, True)
    os.set_inheritable(r_fd, False)

    vid_pipeline = gst_video_pipeline(r_fd)
    aud_pipeline = gst_audio_pipeline(audio_monitor)
    muxer = WFDLPCMMuxer(media.tv_ip, media.sink_rtp_port)
    try:
        muxer.start(vid_pipeline, aud_pipeline)
    except Exception as exc:
        os.close(r_fd)
        os.close(w_fd)
        muxer.stop()
        raise WFDNotReady(f"wf-recorder LPCM muxer failed to start: {exc}") from exc

    wf_cmd = [
        wf_recorder,
        "-y",
        "-D",
        "-r", str(media.config.fps),
        "-o", monitor.name,
        "-c", "rawvideo",
        "-m", "nut",
        "-p", "pix_fmt=yuv420p",
        "-f", "/dev/stdout",
    ]
    ffmpeg_cmd = [
        *_ffmpeg_sender_args(media.config.ffmpeg_stats),
        "-fflags", "+genpts",
        "-thread_queue_size", "1024",
        "-f", "nut",
        "-i", "pipe:0",
        *ffmpeg_libx264_annexb_args(
            media.config,
            src_res=src_res,
            out_res=out_res,
            bitrate=effective_bitrate,
            gop=gop,
            parsed_out_height=parsed_out[1],
        ),
    ]

    print(f"[FluxCast WFD Media] Capturing screen : {monitor.name} ({src_res})")
    print("[FluxCast WFD Media] Video encoder    : libx264 annex-B + WFDLPCMMuxer (0x83)")
    print(f"[FluxCast WFD Media] Capturing audio  : {audio_monitor} (pulsesrc → LPCM mux)")
    if out_res != src_res:
        print(f"[FluxCast WFD Media] Scaling output  : {out_res}")
    print(
        f"[FluxCast WFD Media] RTP target      : "
        f"{media.tv_ip}:{media.sink_rtp_port} from local port {media.config.source_port}"
    )

    wf_proc = None
    ffmpeg_proc = None
    try:
        wf_proc = subprocess.Popen(wf_cmd, stdout=subprocess.PIPE, stderr=None)
        if wf_proc.stdout is None:
            raise WFDNotReady("wf-recorder did not expose stdout.")
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=wf_proc.stdout,
            stdout=w_fd,
            stderr=None,
            pass_fds=(w_fd,),
        )
        wf_proc.stdout.close()
        os.close(w_fd)
        w_fd = -1
        time.sleep(1.0)
        if wf_proc.poll() is not None:
            raise WFDNotReady("wf-recorder exited immediately during LPCM streaming.")
        if ffmpeg_proc.poll() is not None:
            raise WFDNotReady("ffmpeg exited immediately during LPCM annex-B encode.")
    except Exception:
        if w_fd >= 0:
            os.close(w_fd)
        os.close(r_fd)
        if ffmpeg_proc is not None:
            ffmpeg_proc.terminate()
        if wf_proc is not None:
            wf_proc.terminate()
        muxer.stop()
        raise

    media._lpcm_muxer = muxer
    media.processes = [wf_proc, ffmpeg_proc]
