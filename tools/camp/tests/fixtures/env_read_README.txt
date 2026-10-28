env_read_* are byte-exact captures of how a machine answers the inventory read; they are copied, never hand-typed,
so they carry no header of their own. Read them with the tests in test_env_read.py.

Hosts and flavours
  perseus   macOS (Darwin 27.2.0), Tailscale SSH, fish login shell (/opt/homebrew/bin/fish), Docker Desktop, server 29.9.2.
            Tailscale SSH reports exit status 0 whatever the remote command returned; stderr does reach the client.
            Captured over camp's own ssh option set. docker is on the non-interactive PATH (/opt/homebrew/bin/docker).
  andromeda Linux, local subprocess (no ssh), Docker Engine 29.8.0.

Files
  env_read_perseus_tailscale_unknown_command.stderr         fish's reply to a command that does not exist (exit 0, empty stdout)
  env_read_perseus_tailscale_daemon_missing_socket.stderr   DOCKER_HOST=unix:// at a socket file that does not exist (exit 0, empty stdout)
  env_read_perseus_tailscale_daemon_tcp.stderr              DOCKER_HOST=tcp://127.0.0.1:1, nothing listening (exit 0, empty stdout)
  env_read_perseus_tailscale_daemon_no_listener.stderr      DOCKER_HOST=unix:///dev/null, no listener (exit 0, empty stdout)
  env_read_perseus_tailscale_masked_exit.stderr             sh -c 'echo boom >&2; exit 3' (exit 0: the status is masked)
  env_read_andromeda_local_daemon_missing_socket.stderr     same command as the missing-socket row, run locally (exit 1, empty stdout)

UNMEASURED (no capture exists; tests use the planned exit code with the measured Linux stderr above)
  - OpenSSH exit 127 for a missing docker command
  - OpenSSH exit 1 propagating a daemon failure
  - a genuinely stopped Docker Desktop on macOS
