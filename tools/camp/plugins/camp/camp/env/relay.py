"""A relay that holds an environment's standard ports on this machine and carries each connection
to the port the environment published.

Run as ``python -m camp.env.relay -N -o Tag=<tag> -L 127.0.0.1:<standard>:127.0.0.1:<published> ...``
it stands in for an ssh port forward when the environment is on this same machine.

Contract
--------
* Argv is strict: ``-N``, ``-o <value>`` (any value; it only tags the process) and one or more
  ``-L`` specs whose both ends are the literal ``127.0.0.1`` with ASCII-digit ports that satisfy
  ``port_in_range`` (``PORT_MIN``..``PORT_MAX``). Anything else prints a usage line to stderr and
  exits 2 with nothing bound.
* Every standard port is bound on 127.0.0.1 with SO_REUSEADDR, all or none: if one bind fails the
  ones already held are released, ``port <n>: <reason>`` goes to stderr and the exit status is 1.
* Once every port is bound, exactly ``ready\\n`` is written to stdout, then descriptors 0, 1 and 2
  are re-pointed at /dev/null so the relay never writes to a pipe its parent may have closed.
* Each accepted connection is relayed both ways to a fresh connection to its published port
  (5 s connect timeout); an unreachable upstream closes the client at once. At most
  ``MAX_CONNECTIONS`` connections are served at once; further clients wait in the backlog, and
  accept backs off on descriptor exhaustion.
* SIGTERM or SIGINT closes the listeners and every open connection and exits 0.

The relay never dials anywhere but 127.0.0.1: the only address it connects to is the literal
loopback address, and the only addresses it listens on are the same.
"""

from __future__ import annotations

import os
import re
import select
import signal
import socket
import sys
import threading
import time

PORT_MIN = 1024
PORT_MAX = 65535

USAGE = "usage: python -m camp.env.relay -N -o Tag=TAG -L 127.0.0.1:PORT:127.0.0.1:TARGET ..."
MAX_CONNECTIONS_ENV = "CAMP_ENV_RELAY_MAX_CONNECTIONS"

_LOOPBACK = "127.0.0.1"
_BACKLOG = 128
_UPSTREAM_TIMEOUT_SECONDS = 5
_CHUNK = 65536
# Two descriptors per connection (client and upstream): 64 connections hold 128, plus the
# listeners, the wake pair and stdio, well inside macOS's default soft limit of 256.
_DEFAULT_MAX_CONNECTIONS = 64
_ACCEPT_BACKOFF_SECONDS = 0.1
_CAP_POLL_SECONDS = 0.05


def port_in_range(port: int) -> bool:
    return PORT_MIN <= port <= PORT_MAX


def max_connections() -> int:
    try:
        value = int(os.environ.get(MAX_CONNECTIONS_ENV, ""))
    except ValueError:
        return _DEFAULT_MAX_CONNECTIONS
    return value if value >= 1 else _DEFAULT_MAX_CONNECTIONS


def parse_mapping(spec: str) -> tuple[int, int] | None:
    """(standard port, published port) for ``127.0.0.1:<port>:127.0.0.1:<port>``, or None."""
    fields = spec.split(":")
    if len(fields) != 4 or fields[0] != _LOOPBACK or fields[2] != _LOOPBACK:
        return None
    if not all(re.fullmatch(r"[0-9]{1,5}", f) for f in (fields[1], fields[3])):
        return None
    listen, target = int(fields[1]), int(fields[3])
    if not (port_in_range(listen) and port_in_range(target)):
        return None
    return listen, target


def parse_args(args: list[str]) -> tuple[list[tuple[int, int]] | None, str | None]:
    """The (standard, published) pairs, or an error line to print."""
    pairs: list[tuple[int, int]] = []
    i = 0
    while i < len(args):
        if args[i] == "-N":
            i += 1
        elif args[i] in ("-o", "-L") and i + 1 < len(args):
            if args[i] == "-L":
                pair = parse_mapping(args[i + 1])
                if pair is None:
                    return None, (
                        f"-L {args[i + 1]}: both ends must be 127.0.0.1 "
                        f"with ports {PORT_MIN}-{PORT_MAX}"
                    )
                pairs.append(pair)
            i += 2
        else:
            return None, f"{args[i]}: not an argument of relay"
    if not pairs:
        return None, "no -L given"
    return pairs, None


class Relay:
    def __init__(self, pairs: list[tuple[int, int]], cap: int):
        self.pairs = pairs
        self.cap = cap
        self.listeners: list[tuple[socket.socket, int]] = []
        self.lock = threading.Lock()
        self.open: set[socket.socket] = set()
        self.active = 0
        self.stopping = False
        self.wake_read, wake_write = socket.socketpair()
        for which in (signal.SIGTERM, signal.SIGINT):
            signal.signal(which, lambda *_: wake_write.send(b"x"))

    def bind(self) -> tuple[int, str] | None:
        """None once every port is held, otherwise (port, reason) with nothing held."""
        for listen, target in self.pairs:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((_LOOPBACK, listen))
                sock.listen(_BACKLOG)
                sock.setblocking(False)
            except OSError as problem:
                sock.close()
                self.close_listeners()
                return listen, problem.strerror or str(problem)
            self.listeners.append((sock, target))
        return None

    def close_listeners(self) -> None:
        for sock, _ in self.listeners:
            sock.close()
        self.listeners = []

    def track(self, sock: socket.socket) -> bool:
        with self.lock:
            if self.stopping:
                sock.close()
                return False
            self.open.add(sock)
            return True

    def untrack(self, sock: socket.socket) -> None:
        with self.lock:
            self.open.discard(sock)
        sock.close()

    def stop(self) -> None:
        with self.lock:
            self.stopping = True
            doomed = list(self.open)
        self.close_listeners()
        for sock in doomed:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def pump(self, source: socket.socket, sink: socket.socket) -> None:
        try:
            while data := source.recv(_CHUNK):
                sink.sendall(data)
        except OSError:
            pass
        try:
            sink.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    def serve(self, client: socket.socket, target: int) -> None:
        if not self.track(client):
            return
        try:
            upstream = socket.create_connection((_LOOPBACK, target), timeout=_UPSTREAM_TIMEOUT_SECONDS)
        except OSError:
            self.untrack(client)
            return
        upstream.settimeout(None)
        if not self.track(upstream):
            self.untrack(client)
            return
        back = threading.Thread(target=self.pump, args=(upstream, client), daemon=True)
        back.start()
        self.pump(client, upstream)
        back.join()
        self.untrack(client)
        self.untrack(upstream)

    def serve_counted(self, client: socket.socket, target: int) -> None:
        try:
            self.serve(client, target)
        finally:
            with self.lock:
                self.active -= 1

    def run(self) -> None:
        targets = {sock: target for sock, target in self.listeners}
        pause_until = 0.0
        while True:
            now = time.monotonic()
            with self.lock:
                full = self.active >= self.cap
            paused = now < pause_until
            if full or paused:
                # A listener that cannot be accepted from stays readable, so it is left out of
                # the wait; the wake socket is still watched.
                watch: list[socket.socket] = []
                timeout = max(pause_until - now, 0.0) if paused else None
                if full:
                    timeout = _CAP_POLL_SECONDS if timeout is None else min(timeout, _CAP_POLL_SECONDS)
            else:
                watch, timeout = list(targets), None
            ready, _, _ = select.select([self.wake_read, *watch], [], [], timeout)
            if self.wake_read in ready:
                break
            for sock in ready:
                try:
                    client, _ = sock.accept()
                except (BlockingIOError, ConnectionAbortedError):
                    continue
                except OSError:
                    pause_until = time.monotonic() + _ACCEPT_BACKOFF_SECONDS
                    break
                client.setblocking(True)
                with self.lock:
                    self.active += 1
                threading.Thread(
                    target=self.serve_counted, args=(client, targets[sock]), daemon=True
                ).start()
        self.stop()


def detach_stdio() -> None:
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    os.close(null)


def main(argv: list[str]) -> int:
    pairs, problem = parse_args(argv[1:])
    if problem:
        print(f"{problem}\n{USAGE}", file=sys.stderr)
        return 2
    held = Relay(pairs, max_connections())
    failure = held.bind()
    if failure:
        print(f"port {failure[0]}: {failure[1]}", file=sys.stderr)
        return 1
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    detach_stdio()
    held.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
