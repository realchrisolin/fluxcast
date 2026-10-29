"""wf-recorder path into existing WFDLPCMMuxer (Gst appsink)."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wfd.config import WFDMediaConfig  # noqa: E402
from wfd.media.wlroots_lpcm import (  # noqa: E402
    ffmpeg_libx264_annexb_args,
    gst_audio_pipeline,
    gst_video_pipeline,
    want_wf_recorder_lpcm,
)


def _cfg(**kwargs) -> WFDMediaConfig:
    values = dict(monitor=None, fps=30, bitrate="4M", peer_name="TV", no_audio=False)
    values.update(kwargs)
    return WFDMediaConfig(**values)


class WantLpcmTest(unittest.TestCase):
    def test_default_stays_off(self):
        self.assertFalse(want_wf_recorder_lpcm(_cfg(peer_name="Samsung"), env=""))

    def test_microsoft_adapter_on(self):
        self.assertTrue(
            want_wf_recorder_lpcm(_cfg(peer_name="Microsoft Wireless Display Adapter"), env="")
        )

    def test_env_opt_in(self):
        self.assertTrue(want_wf_recorder_lpcm(_cfg(peer_name="hotyeah"), env="1"))

    def test_env_opt_out_wins_over_microsoft(self):
        self.assertFalse(
            want_wf_recorder_lpcm(_cfg(peer_name="Microsoft Display"), env="0")
        )

    def test_no_audio_never(self):
        self.assertFalse(
            want_wf_recorder_lpcm(_cfg(peer_name="Microsoft Display", no_audio=True), env="1")
        )


class PipelineStringsTest(unittest.TestCase):
    def test_video_appsink_h264parse(self):
        text = gst_video_pipeline(7)
        self.assertIn("fdsrc fd=7", text)
        self.assertIn("h264parse", text)
        self.assertIn("appsink name=sink", text)

    def test_audio_pulsesrc_s16be(self):
        text = gst_audio_pipeline("miracast.monitor")
        self.assertIn("pulsesrc device='miracast.monitor'", text)
        self.assertIn("format=S16BE", text)
        self.assertIn("appsink name=sink", text)

    def test_annexb_is_libx264_not_rtp(self):
        args = ffmpeg_libx264_annexb_args(
            _cfg(),
            src_res="1920x1080",
            out_res="1920x1080",
            bitrate="4M",
            gop=30,
            parsed_out_height=1080,
        )
        self.assertIn("libx264", args)
        self.assertIn("h264", args)
        self.assertIn("-an", args)
        self.assertNotIn("rtp_mpegts", args)
        self.assertNotIn("aac", args)


class StartCleanupTest(unittest.TestCase):
    def test_muxer_start_failure_stops_muxer(self):
        from wfd.config import WFDNotReady
        from wfd.media import wlroots_lpcm as mod

        media = SimpleNamespace(
            config=_cfg(peer_name="Microsoft Display", audio_device="alsa.monitor"),
            tv_ip="192.168.49.2",
            sink_rtp_port=7236,
            processes=[],
            _lpcm_muxer=None,
        )
        monitor = SimpleNamespace(name="eDP-1", width=1920, height=1080)
        fake_muxer = mock.Mock()
        fake_muxer.start.side_effect = RuntimeError("no gst")
        with mock.patch("drivers.wfd_lpcm_mux.WFDLPCMMuxer", return_value=fake_muxer):
            with self.assertRaises(WFDNotReady):
                mod.start_wf_recorder_lpcm(media, "/usr/bin/wf-recorder", monitor)
        fake_muxer.stop.assert_called()


if __name__ == "__main__":
    unittest.main()
