"""wf-recorder → existing WFDLPCMMuxer via GStreamer appsink.

Portal already feeds Gst appsink into ``drivers.wfd_lpcm_mux.WFDLPCMMuxer``
for Microsoft adapters. This module does the same on the wf-recorder path:
stock libx264 annex-B plus pulsesrc PCM. ``FLUXCAST_WFD_CAPTURE_ENCODE=dmabuf``
opts into wf-recorder DMA-BUF ``h264_vaapi`` annex-B (no ffmpeg). Default
remains ffmpeg AAC MPEG-TS/RTP, and LPCM without that env stays libx264.
"""

from __future__ import annotations

import os
import shutil
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
    """True for Microsoft adapters, or FLUXCAST_WFD_WF_RECORDER_LPCM=1.

    Off when audio is disabled. Default (no env, non-Microsoft) is False so
    Samsung/AAC sessions stay on ffmpeg mpegts.
    """
    if config.no_audio:
        return False
    raw = (env if env is not None else os.environ.get("FLUXCAST_WFD_WF_RECORDER_LPCM") or "")
    flag = raw.strip().lower()
    if flag in _TRUE:
        return True
    if flag in _FALSE:
        return False
    if getattr(config, "prefer_lpcm", False):
        return True
    return "microsoft" in (config.peer_name or "").lower()


def want_lpcm_dmabuf(env: str | None = None) -> bool:
    """True when FLUXCAST_WFD_CAPTURE_ENCODE=dmabuf|vaapi (LPCM path only)."""
    raw = env if env is not None else os.environ.get("FLUXCAST_WFD_CAPTURE_ENCODE") or ""
    return raw.strip().lower() in ("dmabuf", "vaapi", "dma")


def vaapi_render_device() -> str | None:
    path = (os.environ.get("FLUXCAST_VAAPI_DEVICE") or "").strip() or "/dev/dri/renderD128"
    return path if os.path.exists(path) else None


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


def gst_audio_fd_pipeline(read_fd: int) -> str:
    """S16LE from pw-cat/parec stdout → S16BE for WFD LPCM."""
    return (
        f"fdsrc fd={int(read_fd)} do-timestamp=true ! "
        "audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved ! "
        "audioconvert ! audio/x-raw,format=S16BE ! "
        "appsink name=sink sync=false"
    )


def normalize_sink_monitor(audio_monitor: str) -> str:
    am = (audio_monitor or "").strip()
    if not am:
        raise WFDNotReady("LPCM path requires a Pulse/PipeWire sink monitor")
    if am in ("default", "auto") or "input" in am.lower():
        raise WFDNotReady(
            f"Refusing LPCM capture from {audio_monitor!r}; "
            "need a sink monitor like 'miracast.monitor'"
        )
    if not am.endswith(".monitor"):
        am = f"{am}.monitor"
    return am


def want_pwcat_capture(env: str | None = None) -> bool:
    """pw-cat/parec unless FLUXCAST_WFD_LPCM_PULSESRC=1 forces Gst pulsesrc."""
    raw = env if env is not None else os.environ.get("FLUXCAST_WFD_LPCM_PULSESRC") or ""
    if raw.strip().lower() in _TRUE:
        return False
    return shutil.which("pw-cat") is not None or shutil.which("parec") is not None


def lpcm_audio_capture_cmd(audio_monitor: str) -> list[str]:
    """pw-cat --target binds the .monitor node; media.role=Abstract skips restore."""
    if shutil.which("pw-cat") is not None:
        return [
            "pw-cat",
            "-r",
            "-a",
            "--target", audio_monitor,
            "--rate", "48000",
            "--channels", "2",
            "--format", "s16",
            "-P", "application.name=fluxcast-wfd-capture",
            "-P", f"media.name={audio_monitor}",
            "-P", "media.role=Abstract",
            "-P", "media.category=Capture",
            "-P", f"target.object={audio_monitor}",
            "-",
        ]
    return [
        "parec",
        f"--device={audio_monitor}",
        "--client-name=fluxcast-wfd-capture",
        f"--stream-name={audio_monitor}",
        "--rate=48000",
        "--channels=2",
        "--format=s16le",
        "--raw",
        "--property=media.role=Abstract",
        f"--property=module-stream-restore.id=fluxcast-wfd:{audio_monitor}",
    ]


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


def wf_recorder_dmabuf_cmd(
    wf_recorder: str,
    monitor_name: str,
    *,
    device: str,
    bitrate_bits: int,
    gop: int,
    src_res: str,
    out_res: str,
    out_w: int,
    out_h: int,
) -> list[str]:
    """wf-recorder DMA-BUF h264_vaapi annex-B on stdout. No -r (stock DMA)."""
    if out_res != src_res:
        vf = f"scale_vaapi=w={out_w}:h={out_h}:format=nv12:out_range=tv"
    else:
        vf = "scale_vaapi=format=nv12:out_range=tv"
    return [
        wf_recorder,
        "-y",
        "-D",
        "-o", monitor_name,
        "-c", "h264_vaapi",
        "-d", device,
        "-b", "0",
        "-F", vf,
        "-p", f"b={bitrate_bits}",
        "-p", f"maxrate={bitrate_bits}",
        "-p", "rc_mode=CBR",
        "-p", f"gop_size={gop}",
        "-p", "profile=constrained_baseline",
        "-p", "bf=0",
        "-m", "h264",
        "-f", "/dev/stdout",
    ]


def start_wf_recorder_lpcm(media, wf_recorder: str, monitor) -> None:
    """Start wf-recorder + libx264 annex-B into WFDLPCMMuxer."""
    try:
        from drivers.wfd_lpcm_mux import WFDLPCMMuxer
    except ImportError as exc:
        raise WFDNotReady(f"WFDLPCMMuxer unavailable: {exc}") from exc

    src_res = f"{monitor.width}x{monitor.height}"
    out_res = media.config.output_resolution or src_res
    audio_monitor = normalize_sink_monitor(
        media.config.audio_device or _detect_audio_monitor() or ""
    )
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
    ar_fd = aw_fd = -1
    use_pwcat = want_pwcat_capture()
    if use_pwcat:
        ar_fd, aw_fd = os.pipe()
        os.set_inheritable(aw_fd, True)
        os.set_inheritable(ar_fd, False)

    vid_pipeline = gst_video_pipeline(r_fd)
    aud_pipeline = (
        gst_audio_fd_pipeline(ar_fd) if use_pwcat else gst_audio_pipeline(audio_monitor)
    )
    muxer = WFDLPCMMuxer(
        media.tv_ip,
        media.sink_rtp_port,
        aosp_pids=bool(media.config.aosp_pmt_pid),
    )
    try:
        muxer.start(vid_pipeline, aud_pipeline)
    except Exception as exc:
        os.close(r_fd)
        os.close(w_fd)
        if ar_fd >= 0:
            os.close(ar_fd)
        if aw_fd >= 0:
            os.close(aw_fd)
        muxer.stop()
        raise WFDNotReady(f"wf-recorder LPCM muxer failed to start: {exc}") from exc

    device = vaapi_render_device() if want_lpcm_dmabuf() else None
    use_dmabuf = bool(device)
    if want_lpcm_dmabuf() and not device:
        print(
            "[FluxCast WFD Media] FLUXCAST_WFD_CAPTURE_ENCODE=dmabuf but no "
            "VAAPI render node; falling back to libx264 annex-B",
            flush=True,
        )

    pid_note = (
        "AOSP PIDs PCR=0x1000 VID=0x1011"
        if media.config.aosp_pmt_pid
        else "Microsoft PIDs PCR=VID=0x1000"
    )
    enc_note = (
        "h264_vaapi DMA-BUF annex-B"
        if use_dmabuf
        else "libx264 annex-B"
    )
    print(f"[FluxCast WFD Media] Capturing screen : {monitor.name} ({src_res})")
    print(
        f"[FluxCast WFD Media] Video encoder    : {enc_note} + "
        f"WFDLPCMMuxer (0x83, {pid_note})"
    )
    audio_note = "pw-cat --target → LPCM mux" if use_pwcat else "pulsesrc → LPCM mux"
    print(f"[FluxCast WFD Media] Capturing audio  : {audio_monitor} ({audio_note})")
    if out_res != src_res:
        print(f"[FluxCast WFD Media] Scaling output  : {out_res}")
    print(
        f"[FluxCast WFD Media] RTP target      : "
        f"{media.tv_ip}:{media.sink_rtp_port} from local port {media.config.source_port}"
    )

    wf_proc = None
    ffmpeg_proc = None
    aud_proc = None
    try:
        if use_dmabuf:
            wf_cmd = wf_recorder_dmabuf_cmd(
                wf_recorder,
                monitor.name,
                device=device,
                bitrate_bits=int(effective_kbits) * 1000,
                gop=gop,
                src_res=src_res,
                out_res=out_res,
                out_w=parsed_out[0],
                out_h=parsed_out[1],
            )
            wf_proc = subprocess.Popen(
                wf_cmd,
                stdout=w_fd,
                stderr=None,
                pass_fds=(w_fd,),
            )
            os.close(w_fd)
            w_fd = -1
            time.sleep(1.0)
            if wf_proc.poll() is not None:
                raise WFDNotReady("wf-recorder DMA-BUF exited immediately during LPCM streaming.")
            media.processes = [wf_proc]
        else:
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
            media.processes = [wf_proc, ffmpeg_proc]
        if use_pwcat:
            aud_proc = subprocess.Popen(
                lpcm_audio_capture_cmd(audio_monitor),
                stdout=aw_fd,
                stderr=None,
                pass_fds=(aw_fd,),
            )
            os.close(aw_fd)
            aw_fd = -1
            time.sleep(0.3)
            if aud_proc.poll() is not None:
                raise WFDNotReady("pw-cat/parec exited immediately during LPCM capture.")
            media.processes = list(media.processes) + [aud_proc]
    except Exception:
        if w_fd >= 0:
            os.close(w_fd)
        os.close(r_fd)
        if aw_fd >= 0:
            os.close(aw_fd)
        if ar_fd >= 0:
            os.close(ar_fd)
        if ffmpeg_proc is not None:
            ffmpeg_proc.terminate()
        if wf_proc is not None:
            wf_proc.terminate()
        if aud_proc is not None:
            aud_proc.terminate()
        muxer.stop()
        raise

    media._lpcm_muxer = muxer
