#!/usr/bin/env python3
"""vpn-status/web-server.py: which history events a poll writes.
Usage: python3 tests/test_vpn_status.py"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("vpn_status", ROOT / "vpn-status" / "web-server.py")
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)


def events(polls):
    state = {}
    out = []
    for fwd, qbit in polls:
        out += [e["event"] for e in vs.poll_events(state, fwd, qbit, "1.2.3.4")]
    return out


class PollEvents(unittest.TestCase):
    def test_first_port_is_an_update(self):
        state = {}
        ev = vs.poll_events(state, 45321, 45321, "1.2.3.4")
        self.assertEqual([(e["event"], e["port"], e["old_port"], e["vpn_ip"]) for e in ev], [("port_update", 45321, 0, "1.2.3.4")])

    def test_same_port_writes_nothing(self):
        self.assertEqual(events([(45321, 45321), (45321, 45321), (45321, 45321)]), ["port_update"])

    def test_new_port_is_an_update_with_old_port(self):
        state = {}
        vs.poll_events(state, 45321, 45321, "1.2.3.4")
        ev = vs.poll_events(state, 51000, 51000, "5.6.7.8")
        self.assertEqual([(e["event"], e["port"], e["old_port"], e["vpn_ip"]) for e in ev], [("port_update", 51000, 45321, "5.6.7.8")])

    def test_mismatch_once_until_it_matches_again(self):
        self.assertEqual(events([(45321, 6881), (45321, 6881), (45321, 45321), (45321, 6881)]), ["port_update", "mismatch", "mismatch"])

    def test_zero_port_once_per_outage(self):
        self.assertEqual(events([(45321, 45321), (0, 45321), (None, 45321), (45321, 45321)]), ["port_update", "zero_port"])

    def test_back_after_outage_on_new_port(self):
        self.assertEqual(events([(45321, 45321), (0, 45321), (51000, 51000)]), ["port_update", "zero_port", "port_update"])

    def test_qbit_unreachable_is_not_a_mismatch(self):
        self.assertEqual(events([(45321, None)]), ["port_update"])


if __name__ == "__main__":
    unittest.main()
