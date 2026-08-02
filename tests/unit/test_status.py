"""status.py tests — cmdline parsing with spaces, missing-process detection (audit XXV)."""
import builtins
import sys
from types import SimpleNamespace

from vuka.core import status


def _proc(label, args, pid):
    return (label, args, pid)


class TestMissingProcesses:
    def test_all_expected_running(self):
        procs = [
            _proc("vuka.core.dashboard", "8080", 1),
            _proc("vuka.core.supervisor", "main", 2),
            _proc("vuka.ai.kronos_server", "start", 3),
        ]
        assert status.missing_processes(procs) == []

    def test_reports_missing_sorted(self):
        procs = [_proc("vuka.core.dashboard", "", 1)]
        assert status.missing_processes(procs) == ["kronos_server", "supervisor"]


class TestScanProcesses:
    def _fake_psutil(self):
        return type(
            "FakePsutil",
            (),
            {
                "process_iter": staticmethod(
                    lambda attrs=None: [
                        SimpleNamespace(info={
                            "pid": 1,
                            "name": "python.exe",
                            "cmdline": [
                                "C:\\Program Files\\Python\\python.exe",
                                "-m",
                                "vuka.ai.kronos_server",
                                "start",
                            ],
                        }),
                        SimpleNamespace(info={
                            "pid": 2,
                            "name": "python.exe",
                            "cmdline": [
                                "python",
                                "C:\\Program Files\\Vuka\\run\\vuka.core.dashboard.py",
                                "8080",
                            ],
                        }),
                        SimpleNamespace(info={
                            "pid": 3,
                            "name": "node.exe",
                            "cmdline": ["node", "server.js"],
                        }),
                    ]
                ),
            },
        )

    def test_parses_cmdline_with_spaces(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "psutil", self._fake_psutil())
        procs = status.scan_processes()
        by_pid = {p[2]: p for p in procs}
        assert by_pid[1][0] == "vuka.ai.kronos_server"
        # Script form: the full path (including spaces) is the label, not a
        # whitespace-split fragment.
        assert by_pid[2][0] == "C:\\Program Files\\Vuka\\run\\vuka.core.dashboard.py"
        assert 3 not in by_pid  # non-python process ignored

    def test_returns_empty_when_psutil_unavailable(self, monkeypatch):
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "psutil":
                raise ImportError("psutil not installed")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.delitem(sys.modules, "psutil", raising=False)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert status.scan_processes() == []
