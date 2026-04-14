# tui/watcher.py
from __future__ import annotations
import os
import queue
import select
import threading
from typing import Callable

from parser.models import ProjectStats, SessionStats

# kqueue vnode filter constants (macOS/BSD stdlib)
_KQ_FILTER_VNODE = -4
_KQ_EV_ADD    = 0x0001
_KQ_EV_DELETE = 0x0002
_KQ_EV_CLEAR  = 0x0020
_NOTE_WRITE   = 0x00000002
_NOTE_EXTEND  = 0x00000004


class FileWatcher:
    """
    macOS kqueue-based file system watcher.

    Watches project directories for new JSONL file creation (NOTE_WRITE on
    the directory fd) and individual JSONL files for appends (NOTE_WRITE |
    NOTE_EXTEND on the file fd).

    Runs on a background daemon thread. Commands (add/remove watches) are
    posted via a queue so the Textual main thread never blocks. The kqueue
    loop uses a 0.5 s timeout to drain the command queue between events.

    Callbacks are invoked from the background thread — callers must use
    app.call_from_thread() to re-enter the Textual event loop safely.
    """

    def __init__(
        self,
        on_file_changed: Callable[[str], None],
        on_dir_changed: Callable[[str], None],
    ) -> None:
        self._on_file_changed = on_file_changed
        self._on_dir_changed = on_dir_changed
        self._kq = select.kqueue()
        self._dir_fds: dict[str, int] = {}
        self._file_fds: dict[str, int] = {}
        self._fd_to_path: dict[int, str] = {}
        self._cmd_q: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="kqueue-watcher"
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def watch_dir(self, path: str) -> None:
        self._cmd_q.put(("add_dir", path))

    def watch_file(self, path: str) -> None:
        self._cmd_q.put(("add_file", path))

    def unwatch_file(self, path: str) -> None:
        self._cmd_q.put(("rm_file", path))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._drain_commands()
            try:
                events = self._kq.control(None, 32, 0.5)
            except OSError:
                continue
            for ev in events:
                path = self._fd_to_path.get(ev.ident)
                if path is None:
                    continue
                if path in self._dir_fds:
                    self._on_dir_changed(path)
                else:
                    self._on_file_changed(path)
        self._close_all()

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd, path = self._cmd_q.get_nowait()
            except queue.Empty:
                break
            if cmd == "add_dir":
                self._add_dir(path)
            elif cmd == "add_file":
                self._add_file(path)
            elif cmd == "rm_file":
                self._rm_file(path)

    def _add_dir(self, path: str) -> None:
        if path in self._dir_fds or not os.path.isdir(path):
            return
        fd = os.open(path, os.O_RDONLY)
        self._dir_fds[path] = fd
        self._fd_to_path[fd] = path
        ev = select.kevent(
            fd,
            filter=_KQ_FILTER_VNODE,
            flags=_KQ_EV_ADD | _KQ_EV_CLEAR,
            fflags=_NOTE_WRITE,
        )
        self._kq.control([ev], 0)

    def _add_file(self, path: str) -> None:
        if path in self._file_fds or not os.path.isfile(path):
            return
        fd = os.open(path, os.O_RDONLY)
        self._file_fds[path] = fd
        self._fd_to_path[fd] = path
        ev = select.kevent(
            fd,
            filter=_KQ_FILTER_VNODE,
            flags=_KQ_EV_ADD | _KQ_EV_CLEAR,
            fflags=_NOTE_WRITE | _NOTE_EXTEND,
        )
        self._kq.control([ev], 0)

    def _rm_file(self, path: str) -> None:
        fd = self._file_fds.pop(path, None)
        if fd is None:
            return
        self._fd_to_path.pop(fd, None)
        ev = select.kevent(fd, filter=_KQ_FILTER_VNODE, flags=_KQ_EV_DELETE)
        try:
            self._kq.control([ev], 0)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def _close_all(self) -> None:
        for fd in list(self._file_fds.values()) + list(self._dir_fds.values()):
            try:
                os.close(fd)
            except OSError:
                pass
        self._file_fds.clear()
        self._dir_fds.clear()
        self._fd_to_path.clear()
        try:
            self._kq.close()
        except OSError:
            pass


def latest_jsonl_path(projects: list[ProjectStats]) -> str | None:
    """Return the jsonl_path of the session with the most recent mtime.

    Scans all loaded sessions. Returns None if no loaded sessions exist.
    """
    latest_path: str | None = None
    latest_mtime = 0.0
    for project in projects:
        if not project.loaded:
            continue
        for session in project.sessions:
            if not session.jsonl_path:
                continue
            try:
                mtime = os.path.getmtime(session.jsonl_path)
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest_path = session.jsonl_path
            except OSError:
                pass
    return latest_path


def find_session_by_path(
    projects: list[ProjectStats], jsonl_path: str
) -> tuple[ProjectStats | None, SessionStats | None]:
    """Return (project, session) for the given jsonl_path, or (None, None)."""
    for project in projects:
        for session in project.sessions:
            if session.jsonl_path == jsonl_path:
                return project, session
    return None, None
