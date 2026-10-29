"""WFD audio codec choice: AAC default, LPCM for LPCM-only sinks."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wfd.audio import (  # noqa: E402
    AUDIO_AAC,
    AUDIO_LPCM,
    AUDIO_NONE,
    choose_wfd_audio,
    rtsp_audio_line,
)
from wfd.constants import WFD_AUDIO_AAC, WFD_AUDIO_LPCM_48K  # noqa: E402


class ChooseAudioTest(unittest.TestCase):
    def test_samsung_aac_stays_aac(self):
        self.assertEqual(
            choose_wfd_audio("AAC 00000001 00", peer_name="Samsung", force_aac=False, prefer_lpcm=False),
            AUDIO_AAC,
        )

    def test_lpcm_only_dongle(self):
        self.assertEqual(
            choose_wfd_audio("LPCM 00000002 00", peer_name="hotyeah", force_aac=False, prefer_lpcm=False),
            AUDIO_LPCM,
        )

    def test_both_listed_defaults_to_aac(self):
        self.assertEqual(
            choose_wfd_audio(
                "LPCM 00000002 00, AAC 00000001 00",
                peer_name="TV",
                force_aac=False,
                prefer_lpcm=False,
            ),
            AUDIO_AAC,
        )

    def test_prefer_lpcm_when_both_listed(self):
        self.assertEqual(
            choose_wfd_audio(
                "LPCM 00000002 00, AAC 00000001 00",
                peer_name="TV",
                force_aac=False,
                prefer_lpcm=True,
            ),
            AUDIO_LPCM,
        )

    def test_microsoft_is_lpcm(self):
        self.assertEqual(
            choose_wfd_audio("AAC 00000001 00", peer_name="Microsoft Display", force_aac=False, prefer_lpcm=False),
            AUDIO_LPCM,
        )

    def test_force_aac_wins(self):
        self.assertEqual(
            choose_wfd_audio("LPCM 00000002 00", peer_name="hotyeah", force_aac=True, prefer_lpcm=True),
            AUDIO_AAC,
        )

    def test_unknown_caps_are_video_only(self):
        self.assertEqual(
            choose_wfd_audio("AC3 00000001 00", peer_name="TV", force_aac=False, prefer_lpcm=False),
            AUDIO_NONE,
        )

    def test_empty_caps_default_aac(self):
        self.assertEqual(
            choose_wfd_audio("", peer_name="TV", force_aac=False, prefer_lpcm=False),
            AUDIO_AAC,
        )

    def test_rtsp_lines(self):
        self.assertEqual(rtsp_audio_line(AUDIO_LPCM), WFD_AUDIO_LPCM_48K)
        self.assertEqual(rtsp_audio_line(AUDIO_AAC), WFD_AUDIO_AAC)
        self.assertEqual(rtsp_audio_line(AUDIO_NONE), "none")


if __name__ == "__main__":
    unittest.main()
