"""Opt-in wf-recorder ffmpeg h264_vaapi (issue #19)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wfd.config import WFDMediaConfig  # noqa: E402
from wfd.hw_vaapi import (  # noqa: E402
    ffmpeg_h264_pipe_args,
    resolve_encoder,
    vaapi_ffmpeg_global_args,
    vaapi_render_device,
    want_wf_recorder_vaapi,
)


def _cfg(**kwargs) -> WFDMediaConfig:
    values = dict(
        monitor=None,
        fps=30,
        bitrate="4M",
        encoder="x264",
        h264_profile="baseline",
        peer_name="TV",
    )
    values.update(kwargs)
    return WFDMediaConfig(**values)


class ResolveEncoderTest(unittest.TestCase):
    def test_default_x264(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLUXCAST_WFD_ENCODER", None)
            self.assertEqual(resolve_encoder("x264", env=""), "x264")

    def test_cli_vaapi(self):
        self.assertEqual(resolve_encoder("vaapi", env=""), "vaapi")

    def test_env_fills_default_cli(self):
        self.assertEqual(resolve_encoder("x264", env="vaapi"), "vaapi")

    def test_explicit_cli_wins_over_env(self):
        self.assertEqual(resolve_encoder("va", env="vaapi"), "va")

    def test_portal_va_unchanged(self):
        self.assertEqual(resolve_encoder("va", env=""), "va")


class PipeArgsTest(unittest.TestCase):
    def test_default_is_libx264(self):
        args, label, used = ffmpeg_h264_pipe_args(
            _cfg(),
            src_res="1920x1080",
            out_res="1920x1080",
            bitrate="4M",
            gop=30,
            parsed_out_height=1080,
        )
        self.assertEqual(label, "libx264")
        self.assertFalse(used)
        self.assertIn("libx264", args)
        self.assertNotIn("h264_vaapi", args)

    def test_vaapi_uses_h264_vaapi_and_hwupload(self):
        args, label, used = ffmpeg_h264_pipe_args(
            _cfg(encoder="vaapi"),
            src_res="1920x1080",
            out_res="1920x1080",
            bitrate="4M",
            gop=30,
            parsed_out_height=1080,
            vaapi_device="/dev/dri/renderD128",
            has_h264_vaapi=True,
        )
        self.assertEqual(label, "h264_vaapi")
        self.assertTrue(used)
        self.assertIn("h264_vaapi", args)
        self.assertIn("format=nv12,hwupload", args)
        self.assertNotIn("libx264", args)
        self.assertEqual(
            vaapi_ffmpeg_global_args("/dev/dri/renderD128")[1],
            "vaapi=va:/dev/dri/renderD128",
        )

    def test_vaapi_falls_back_without_device(self):
        args, label, used = ffmpeg_h264_pipe_args(
            _cfg(encoder="vaapi"),
            src_res="1920x1080",
            out_res="1920x1080",
            bitrate="4M",
            gop=30,
            parsed_out_height=1080,
            vaapi_device=None,
            has_h264_vaapi=True,
        )
        self.assertEqual(label, "libx264")
        self.assertFalse(used)
        self.assertIn("libx264", args)

    def test_want_flag(self):
        self.assertTrue(want_wf_recorder_vaapi("vaapi"))
        self.assertFalse(want_wf_recorder_vaapi("va"))
        self.assertFalse(want_wf_recorder_vaapi("x264"))

    def test_render_device_missing(self):
        with mock.patch("wfd.hw_vaapi.Path.exists", return_value=False):
            self.assertIsNone(vaapi_render_device())


if __name__ == "__main__":
    unittest.main()
