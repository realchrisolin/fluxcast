"""AOSP vs Microsoft PID layout in WFDLPCMMuxer."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from drivers.wfd_lpcm_mux import (  # noqa: E402
    PID_AUD,
    PID_PCR,
    PID_VID,
    PID_VID_AOSP,
    WFDLPCMMuxer,
    _build_pmt,
    lpcm_pid_layout,
)


def _pmt_stream_pids(pkt: bytes) -> list[tuple[int, int]]:
    """(stream_type, elementary_pid) from a 188-byte PMT TS packet."""
    assert pkt[0] == 0x47
    pointer = pkt[4]
    section = pkt[5 + pointer :]
    # skip table_id .. program_info_length
    prog_info_len = ((section[10] & 0x0F) << 8) | section[11]
    pos = 12 + prog_info_len
    end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4  # before CRC
    out: list[tuple[int, int]] = []
    while pos + 5 <= end:
        stype = section[pos]
        epid = ((section[pos + 1] & 0x1F) << 8) | section[pos + 2]
        es_len = ((section[pos + 3] & 0x0F) << 8) | section[pos + 4]
        out.append((stype, epid))
        pos += 5 + es_len
    return out


def _pmt_pcr_pid(pkt: bytes) -> int:
    pointer = pkt[4]
    section = pkt[5 + pointer :]
    return ((section[8] & 0x1F) << 8) | section[9]


def _pmt_version_byte(pkt: bytes) -> int:
    pointer = pkt[4]
    section = pkt[5 + pointer :]
    return section[5]


class LayoutTest(unittest.TestCase):
    def test_default_microsoft_shares_pcr_with_video(self):
        pcr, vid, aud, ver = lpcm_pid_layout(False)
        self.assertEqual((pcr, vid, aud, ver), (0x1000, 0x1000, 0x1100, 0xC1))
        muxer = WFDLPCMMuxer("127.0.0.1", 9)
        self.assertFalse(muxer.aosp_pids)
        self.assertEqual(muxer.pid_vid, PID_VID)
        muxer._sock.close()

    def test_aosp_splits_video_from_pcr(self):
        pcr, vid, aud, ver = lpcm_pid_layout(True)
        self.assertEqual((pcr, vid, aud, ver), (0x1000, 0x1011, 0x1100, 0xC3))
        muxer = WFDLPCMMuxer("127.0.0.1", 9, aosp_pids=True)
        self.assertEqual(muxer.pid_vid, PID_VID_AOSP)
        self.assertEqual(muxer.pid_pcr, PID_PCR)
        muxer._sock.close()

    def test_pmt_default_video_pid_1000(self):
        pkt = _build_pmt()
        streams = _pmt_stream_pids(pkt)
        self.assertEqual(streams[0], (0x1B, PID_VID))
        self.assertEqual(streams[1], (0x83, PID_AUD))
        self.assertEqual(_pmt_pcr_pid(pkt), PID_PCR)
        self.assertEqual(_pmt_version_byte(pkt), 0xC1)

    def test_pmt_aosp_video_pid_1011(self):
        pcr, vid, aud, ver = lpcm_pid_layout(True)
        pkt = _build_pmt(pid_vid=vid, pid_aud=aud, pid_pcr=pcr, version_byte=ver)
        streams = _pmt_stream_pids(pkt)
        self.assertEqual(streams[0], (0x1B, PID_VID_AOSP))
        self.assertEqual(_pmt_pcr_pid(pkt), PID_PCR)
        self.assertNotEqual(streams[0][1], _pmt_pcr_pid(pkt))
        self.assertEqual(_pmt_version_byte(pkt), 0xC3)


if __name__ == "__main__":
    unittest.main()
