from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import nova.api.bridge as bridge
from nova import __version__


def _fake_clock(monkeypatch):
    """Monotonic fake clock so the bridge loops terminate instantly."""
    clock = [0.0]
    monkeypatch.setattr(bridge.time, 'time', lambda: clock[0])
    monkeypatch.setattr(bridge.time, 'sleep', lambda secs: None)
    return clock


def _args(tmp_path):
    return SimpleNamespace(app_root=str(tmp_path), wait=0.01)


class TestProbe:
    def test_probe_matches_expected_version(self, monkeypatch):
        class Response:
            status = 200

            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        versioned = json.dumps({'version': __version__}).encode()
        stale = json.dumps({'version': '0.17.2'}).encode()

        def opener_for(body):
            class Opener:
                def open(self, url, timeout=None):
                    return Response(body)
            return lambda *args, **kwargs: Opener()

        monkeypatch.setattr(bridge.urllib.request, 'build_opener', opener_for(versioned))
        assert bridge._probe('http://x', expected_version=__version__) is True
        assert bridge._probe('http://x', expected_version={'no': 'json'}) is False

        monkeypatch.setattr(bridge.urllib.request, 'build_opener', opener_for(stale))
        assert bridge._probe('http://x', expected_version=__version__) is False
        assert bridge._probe('http://x', expected_version=None) is True

    def test_probe_rejects_broken_json(self, monkeypatch):
        class Response:
            status = 200

            def read(self):
                return b'not json'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Opener:
            def open(self, url, timeout=None):
                return Response()

        monkeypatch.setattr(bridge.urllib.request, 'build_opener', lambda *args, **kwargs: Opener())
        assert bridge._probe('http://x', expected_version=__version__) is False


class TestStart:
    def test_already_running_matching_version_short_circuits(self, monkeypatch, tmp_path):
        calls = []
        probes = iter([True])
        monkeypatch.setattr(bridge, '_server_base', lambda args: ('127.0.0.1', '18000'))
        monkeypatch.setattr(bridge, '_probe', lambda base, timeout=2.0, expected_version=None: calls.append(('probe', expected_version is not None)) or next(probes, False))
        monkeypatch.setattr(bridge, '_spawn_server', lambda args: calls.append(('spawn',)))
        monkeypatch.setattr(bridge, '_stop_conflicting_core', lambda: calls.append(('stop',)))
        _fake_clock(monkeypatch)

        assert bridge._do_start(_args(tmp_path)) == 0
        assert ('spawn',) not in calls
        assert ('stop',) not in calls

    def test_mismatch_stops_obsolete_core_and_starts_fresh(self, monkeypatch, tmp_path):
        calls = []
        # expected, expected, any(blocked), any(free), verify-free, poll expected
        probes = iter([False, False, True, False, False, True])
        monkeypatch.setattr(bridge, '_server_base', lambda args: ('127.0.0.1', '18000'))
        monkeypatch.setattr(bridge, '_probe', lambda base, timeout=2.0, expected_version=None: calls.append(('probe', expected_version is not None)) or next(probes, False))
        monkeypatch.setattr(bridge, '_spawn_server', lambda args: calls.append(('spawn',)))
        monkeypatch.setattr(bridge, '_stop_conflicting_core', lambda: calls.append(('stop',)) or True)
        _fake_clock(monkeypatch)

        assert bridge._do_start(_args(tmp_path)) == 0
        assert ('stop',) in calls
        assert ('spawn',) in calls

    def test_mismatch_aborts_if_conflict_cannot_be_stopped(self, monkeypatch, tmp_path):
        calls = []
        probes = iter([False, False, True])
        monkeypatch.setattr(bridge, '_server_base', lambda args: ('127.0.0.1', '18000'))
        monkeypatch.setattr(bridge, '_probe', lambda base, timeout=2.0, expected_version=None: calls.append(('probe', expected_version is not None)) or next(probes, False))
        monkeypatch.setattr(bridge, '_spawn_server', lambda args: calls.append(('spawn',)))
        monkeypatch.setattr(bridge, '_stop_conflicting_core', lambda: calls.append(('stop',)) or False)
        _fake_clock(monkeypatch)

        assert bridge._do_start(_args(tmp_path)) == 1
        assert ('stop',) in calls
        assert ('spawn',) not in calls

    def test_start_times_out_without_matching_version(self, monkeypatch, tmp_path):
        calls = []
        clock = _fake_clock(monkeypatch)
        monkeypatch.setattr(bridge, '_server_base', lambda args: ('127.0.0.1', '18000'))

        def fake_probe(base, timeout=2.0, expected_version=None):
            calls.append(('probe', expected_version is not None))
            clock[0] += 1.0
            return False

        def fake_stop():
            calls.append(('stop',))
            return True

        monkeypatch.setattr(bridge, '_probe', fake_probe)
        monkeypatch.setattr(bridge, '_spawn_server', lambda args: calls.append(('spawn',)))
        monkeypatch.setattr(bridge, '_stop_conflicting_core', fake_stop)
        monkeypatch.setattr('builtins.print', lambda *args: None)

        assert bridge._do_start(_args(tmp_path)) == 1
        # never accepted a wrong-version backend as "started"
        assert ('spawn',) in calls

    def test_start_when_nothing_running_still_waits_for_version(self, monkeypatch, tmp_path):
        calls = []
        clock = _fake_clock(monkeypatch)
        monkeypatch.setattr(bridge, '_server_base', lambda args: ('127.0.0.1', '18000'))

        def fake_probe(base, timeout=2.0, expected_version=None):
            calls.append(('probe', expected_version is not None))
            clock[0] += 1.0
            return False

        monkeypatch.setattr(bridge, '_probe', fake_probe)
        monkeypatch.setattr(bridge, '_spawn_server', lambda args: calls.append(('spawn',)))
        monkeypatch.setattr(bridge, '_stop_conflicting_core', lambda: calls.append(('stop',)) or True)
        monkeypatch.setattr('builtins.print', lambda *args: None)

        assert bridge._do_start(_args(tmp_path)) == 1
        assert ('stop',) not in calls
        assert ('spawn',) in calls