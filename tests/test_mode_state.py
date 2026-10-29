"""Optional FLUXCAST_WFD_MODE_STATE JSON for external UIs."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wfd.config import WFDCEAMode, WFDVideoFormat  # noqa: E402
from wfd.constants import (  # noqa: E402
    WFD_CEA_720P30,
    WFD_CEA_1080P30,
    WFD_CEA_1080P60,
    WFD_VESA_1200P30,
)
from wfd import mode_state  # noqa: E402


def _sink(cea_mask: int, vesa_mask: int = 0, level: str = "08") -> WFDVideoFormat:
    return WFDVideoFormat(
        native="00",
        preferred="00",
        profile="01",
        level=level,
        cea_mask=cea_mask,
        vesa_mask=vesa_mask,
        hh_mask=0,
    )


class SupportedModesTest(unittest.TestCase):
    def test_none_sink_returns_empty(self):
        self.assertEqual(mode_state.supported_modes(None), [])

    def test_filters_to_advertised_cea_bits(self):
        sink = _sink(WFD_CEA_720P30 | WFD_CEA_1080P30)
        ids = [m["id"] for m in mode_state.supported_modes(sink)]
        self.assertEqual(ids, ["1280x720p30", "1920x1080p30"])

    def test_includes_advertised_vesa_modes(self):
        sink = _sink(cea_mask=0, vesa_mask=WFD_VESA_1200P30)
        ids = [m["id"] for m in mode_state.supported_modes(sink)]
        self.assertIn("1920x1200p30", ids)

    def test_cea_bits_not_gated_by_short_level_bitmap(self):
        sink = _sink(WFD_CEA_720P30 | WFD_CEA_1080P30 | WFD_CEA_1080P60, level="08")
        ids = [m["id"] for m in mode_state.supported_modes(sink)]
        self.assertIn("1920x1080p60", ids)

    def test_stable_sort_by_height_width_fps(self):
        sink = _sink(WFD_CEA_720P30 | WFD_CEA_1080P30)
        modes = mode_state.supported_modes(sink)
        self.assertEqual(
            [(m["height"], m["width"], m["fps"]) for m in modes],
            sorted((m["height"], m["width"], m["fps"]) for m in modes),
        )


class WriteModeStateTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("FLUXCAST_WFD_MODE_STATE", None)

    def test_noop_without_env_path(self):
        os.environ.pop("FLUXCAST_WFD_MODE_STATE", None)
        mode_state.write_mode_state(sink_format=None, current=None)

    def test_writes_json_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "modes.json")
            os.environ["FLUXCAST_WFD_MODE_STATE"] = path
            sink = _sink(WFD_CEA_720P30)
            current = WFDCEAMode("1280x720p30", WFD_CEA_720P30, "28", 1280, 720, 30)
            mode_state.write_mode_state(
                sink_format=sink,
                current=current,
                peer_name="tv",
                peer="aa:bb",
            )
            with open(path, encoding="utf-8") as fh:
                payload = json.loads(fh.read())
            self.assertEqual(payload["current"], "1280x720p30")
            self.assertEqual(payload["peerName"], "tv")
            self.assertEqual(payload["peer"], "aa:bb")
            self.assertEqual(payload["supported"][0]["id"], "1280x720p30")
            self.assertTrue(payload["ceaMask"].startswith("0x"))

    def test_write_oserror_is_swallowed(self):
        os.environ["FLUXCAST_WFD_MODE_STATE"] = "/proc/does-not-allow-writes/modes.json"
        with mock.patch("builtins.open", side_effect=OSError("denied")):
            mode_state.write_mode_state(
                sink_format=_sink(WFD_CEA_720P30),
                current=None,
            )


if __name__ == "__main__":
    unittest.main()
