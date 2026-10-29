import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wfd.wf_recorder import (  # noqa: E402
    find_wf_recorder,
    wf_capture_rate_args,
    wf_recorder_supports_icc,
)


class FindWfRecorderTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("FLUXCAST_WFD_WF_RECORDER_BIN", None)
        os.environ.pop("FLUXCAST_WFD_WF_RECORDER_PROTO", None)
        os.environ.pop("FLUXCAST_WFD_ICC_CAPTURE_FPS", None)
        from wfd import wf_recorder as wr

        wr._icc_cache.clear()

    @mock.patch("wfd.wf_recorder.shutil.which", return_value=None)
    def test_missing_recorder(self, _which):
        self.assertIsNone(find_wf_recorder())

    @mock.patch("wfd.wf_recorder.subprocess.run")
    @mock.patch("wfd.wf_recorder.shutil.which", return_value="/app/usr/bin/wf-recorder")
    def test_rejects_wrapper_when_system_recorder_is_missing(self, _which, run):
        run.return_value = subprocess.CompletedProcess([], 127, "", "not found")
        self.assertIsNone(find_wf_recorder())

    @mock.patch("wfd.wf_recorder.subprocess.run")
    @mock.patch("wfd.wf_recorder.shutil.which", return_value="/usr/bin/wf-recorder")
    def test_accepts_working_recorder(self, _which, run):
        run.return_value = subprocess.CompletedProcess([], 0, "wf-recorder 0.6.0", "")
        self.assertEqual(find_wf_recorder(), "/usr/bin/wf-recorder")

    @mock.patch("wfd.wf_recorder.subprocess.run")
    @mock.patch("wfd.wf_recorder.shutil.which", return_value="/usr/bin/wf-recorder")
    def test_accepts_recorder_without_version_flag(self, _which, run):
        # Older builds may not support --version; only exit 127 is a hard reject.
        run.return_value = subprocess.CompletedProcess([], 1, "", "unknown option")
        self.assertEqual(find_wf_recorder(), "/usr/bin/wf-recorder")

    @mock.patch("wfd.wf_recorder.subprocess.run")
    @mock.patch("wfd.wf_recorder.shutil.which", return_value="/usr/bin/wf-recorder")
    def test_bin_override_preferred(self, _which, run):
        run.return_value = subprocess.CompletedProcess([], 0, "wf-recorder icc", "")
        os.environ["FLUXCAST_WFD_WF_RECORDER_BIN"] = "/opt/wf-recorder-icc"
        self.assertEqual(find_wf_recorder(), "/opt/wf-recorder-icc")

    @mock.patch("wfd.wf_recorder.wf_recorder_supports_icc", return_value=False)
    @mock.patch("wfd.wf_recorder.subprocess.run")
    @mock.patch("wfd.wf_recorder.shutil.which", return_value="/usr/bin/wf-recorder")
    def test_proto_icc_rejects_stock(self, _which, run, _icc):
        run.return_value = subprocess.CompletedProcess([], 0, "wf-recorder 0.6.0", "")
        os.environ["FLUXCAST_WFD_WF_RECORDER_PROTO"] = "icc"
        self.assertIsNone(find_wf_recorder())

    @mock.patch("wfd.wf_recorder.subprocess.run")
    def test_supports_icc_detects_toplevel(self, run):
        def side_effect(cmd, **kwargs):
            if "--help" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "  -t, --toplevel[=ID]\n", "")
            return subprocess.CompletedProcess(cmd, 0, "wf-recorder 0.6.0-bf567c2\n", "")

        run.side_effect = side_effect
        self.assertTrue(wf_recorder_supports_icc("/opt/icc"))
        self.assertTrue(wf_recorder_supports_icc("/opt/icc"))  # cached

    def test_dma_omits_r(self):
        self.assertEqual(
            wf_capture_rate_args("/usr/bin/wf-recorder", 60, vaapi_dmabuf=True),
            [],
        )

    def test_stock_raw_keeps_r(self):
        self.assertEqual(wf_capture_rate_args("/usr/bin/wf-recorder", 60), ["-r", "60"])


if __name__ == "__main__":
    unittest.main()
