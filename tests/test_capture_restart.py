"""Desktop capture rebind (SIGUSR1 / restart_video)."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wfd.config import WFDMediaConfig  # noqa: E402
from wfd.media.pipeline import WFDMediaPipeline  # noqa: E402
from wfd.rtsp.rtsp_server import WFDRTSPServer  # noqa: E402


class RestartActiveMediaTest(unittest.TestCase):
    def test_restart_active_media_invokes_restart_video(self):
        srv = WFDRTSPServer(media_config=WFDMediaConfig(monitor=None), peer_address="aa:bb")
        media = mock.Mock()
        media.restarting = False
        srv._register_media(media)
        self.assertEqual(srv.restart_active_media(), 1)
        media.restart_video.assert_called_once()

    def test_restart_active_media_skips_in_flight(self):
        srv = WFDRTSPServer(media_config=WFDMediaConfig(monitor=None), peer_address="aa:bb")
        media = mock.Mock()
        media.restarting = True
        srv._register_media(media)
        self.assertEqual(srv.restart_active_media(), 0)
        media.restart_video.assert_not_called()


class RestartVideoDesktopTest(unittest.TestCase):
    def test_desktop_restart_clears_dead_senders_and_relaunches(self):
        cfg = WFDMediaConfig(
            monitor=SimpleNamespace(name="HEADLESS-1", width=1920, height=1080)
        )
        pipe = WFDMediaPipeline(cfg, "10.0.0.2", "10.0.0.1", 5000)
        dead = mock.Mock()
        dead.poll.return_value = 1
        dead.wait.return_value = None
        pipe.processes = [dead]
        with mock.patch.object(pipe, "_start_desktop") as start, mock.patch(
            "wfd.media.pipeline.time.sleep"
        ):
            pipe.restart_video()
            start.assert_called_once()
        self.assertFalse(pipe.restarting)

    def test_restarting_flag_set_during_desktop_rebind(self):
        cfg = WFDMediaConfig(
            monitor=SimpleNamespace(name="H", width=1280, height=720)
        )
        pipe = WFDMediaPipeline(cfg, "10.0.0.2", "10.0.0.1", 5000)
        pipe.processes = []
        seen = {}

        def _start():
            seen["restarting"] = pipe.restarting

        with mock.patch.object(pipe, "_start_desktop", side_effect=_start), mock.patch(
            "wfd.media.pipeline.time.sleep"
        ):
            pipe.restart_video()
        self.assertTrue(seen.get("restarting"))
        self.assertFalse(pipe.restarting)

    def test_portal_path_still_respawns_from_retained_fd(self):
        cfg = WFDMediaConfig(monitor=None)
        pipe = WFDMediaPipeline(cfg, "10.0.0.2", "10.0.0.1", 5000)
        pipe._portal_gst_cmd = ["gst-launch-1.0", "fake"]
        pipe._portal_pw_fd = 7
        alive = mock.Mock()
        alive.poll.return_value = None
        alive.wait.return_value = None
        pipe.processes = [alive]
        with mock.patch("wfd.media.pipeline.subprocess.Popen") as popen:
            popen.return_value = mock.Mock()
            pipe.restart_video()
            popen.assert_called_once()
            self.assertEqual(popen.call_args.kwargs["pass_fds"], (7,))

    def test_coalesce_second_caller(self):
        cfg = WFDMediaConfig(monitor=None)
        pipe = WFDMediaPipeline(cfg, "10.0.0.2", "10.0.0.1", 5000)
        pipe._restart_lock.acquire()
        try:
            with mock.patch.object(pipe, "_start_desktop") as start:
                pipe.restart_video()
                start.assert_not_called()
        finally:
            pipe._restart_lock.release()


class SessionSigusr1WiringTest(unittest.TestCase):
    def test_session_registers_sigusr1_and_rebind_loop(self):
        root = os.path.join(os.path.dirname(__file__), "..", "src", "wfd")
        with open(os.path.join(root, "session.py"), encoding="utf-8") as fh:
            session_src = fh.read()
        with open(os.path.join(root, "media", "pipeline.py"), encoding="utf-8") as fh:
            pipeline_src = fh.read()
        with open(os.path.join(root, "rtsp", "handler.py"), encoding="utf-8") as fh:
            handler_src = fh.read()
        self.assertIn("signal.SIGUSR1", session_src)
        self.assertIn("restart_active_media", session_src)
        self.assertIn("Capture restart finished", session_src)
        self.assertIn("Desktop capture pipeline restarted", pipeline_src)
        self.assertIn("self.restarting", pipeline_src)
        self.assertIn('headers={"Session": self.session_id}', handler_src)


if __name__ == "__main__":
    unittest.main()
