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

env_inventory_ports_* are byte-exact `{{json .Ports}}` renderings, line 1 a running container, line 2 the same container created but stopped.
Command: docker ps -a --filter name=<probe> --format '{{json .Ports}}'
Container: alpine, -p 127.0.0.1:28080-28081:8080-8081 -p 127.0.0.1:28090:9090 -p 127.0.0.1:28095:9095/udp
  env_inventory_ports_docker_desktop_4_97_0.txt          perseus, Docker Desktop 4.97.0, server 29.9.2 (running via `docker run -d --rm`, stopped via `docker create`)
  env_inventory_ports_docker_engine_29_8_0_linux.txt     andromeda, Docker Engine 29.8.0 (same two commands)

env_forward_* (K2: ssh -f -N -L forward, captured on andromeda -> tduffield@perseus)
  andromeda Linux, OpenSSH_9.6p1 Ubuntu-3ubuntu13.20, OpenSSL 3.0.13; destination perseus over Tailscale SSH.
  Namespace camp-env-k2probe (never camp-env). The Tag value is a probe tag, the uid is the real local uid.
  env_forward_ps_linux_openssh_9_6.txt              the `ps -axwwo pid=,uid=,command=` line of the backgrounded forward (full argv kept; "-o Tag=..." appears as two argv words, "-L" spec as one)
  env_forward_bind_failure_linux_openssh_9_6.txt    local bind failure with ExitOnForwardFailure=yes: exit 255, stderr verbatim, nothing left running
  Also measured, no fixture: with ssh -f the backgrounded child holds fds 0,1,2 on /dev/null (not the caller's pipes), so a subprocess.run
  with stdout/stderr=PIPE returned in 0.04s (no hang); stderr after backgrounding is lost. Traffic through the forward to a perseus loopback listener returned HTTP 200.
UNMEASURED
  - the Mac ssh (OpenSSH_10.5p1) behaviour for -f / bind failure / ps line: perseus has no sshd on port 22 (Tailscale SSH only), so a loopback ssh -f from perseus is refused

env_inventory_header_* and env_inventory_command.txt, env_inventory_records.txt (five-field format, `|{{json .Ports}}` appended)
  Header measured with the five-field command above on andromeda (Docker Engine 29.8.0) and perseus (Docker Desktop 4.97.0):
  both print `NAMES|STATE|group|name|PORTS`. The records file gains a `""` fifth
  field because its containers (k1probe-*) published no ports.
