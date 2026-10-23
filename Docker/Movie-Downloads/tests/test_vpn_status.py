#!/usr/bin/env python3
"""vpn-status/web-server.py: which history events a poll writes, and the port fix.
Usage: python3 tests/test_vpn_status.py"""
import http.server
import importlib.util
import pathlib
import threading
import unittest
import urllib.parse

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


class NeedsPortFix(unittest.TestCase):
    def test_mismatch_needs_fix(self):
        self.assertTrue(vs.needs_port_fix(45321, 6881))

    def test_match_needs_nothing(self):
        self.assertFalse(vs.needs_port_fix(45321, 45321))

    def test_no_forwarded_port_needs_nothing(self):
        self.assertFalse(vs.needs_port_fix(0, 6881))
        self.assertFalse(vs.needs_port_fix(None, 6881))

    def test_qbit_unreachable_needs_nothing(self):
        self.assertFalse(vs.needs_port_fix(45321, None))


class FixPort(unittest.TestCase):
    def setUp(self):
        self.requests = []
        reqs = self.requests

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                reqs.append((self.path, urllib.parse.unquote_plus(body)))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"Ok")

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.old_url = vs.QBIT_URL
        vs.QBIT_URL = f"http://127.0.0.1:{self.srv.server_port}"

    def tearDown(self):
        vs.QBIT_URL = self.old_url
        self.srv.shutdown()
        self.srv.server_close()

    def test_sets_port_then_reannounces(self):
        self.assertTrue(vs.fix_port(45321))
        self.assertEqual(self.requests, [
            ("/api/v2/app/setPreferences", 'json={"listen_port": 45321}'),
            ("/api/v2/torrents/reannounce", "hashes=all"),
        ])

    def test_unreachable_qbit_is_false(self):
        vs.QBIT_URL = "http://127.0.0.1:1"
        self.assertFalse(vs.fix_port(45321))


class QbitView(unittest.TestCase):
    def test_fields_from_transfer_info_and_preferences(self):
        transfer = {"connection_status": "connected", "dht_nodes": 368, "last_external_address_v4": "5.6.7.8"}
        prefs = {"current_network_interface": "tun0", "listen_port": 45321}
        self.assertEqual(vs.qbit_view(transfer, prefs), {"qbit_external_ip": "5.6.7.8", "qbit_connection": "connected", "qbit_interface": "tun0", "dht_nodes": 368})

    def test_unreachable_qbit_gives_nones(self):
        self.assertEqual(vs.qbit_view(None, None), {"qbit_external_ip": None, "qbit_connection": None, "qbit_interface": None, "dht_nodes": None})


if __name__ == "__main__":
    unittest.main()
