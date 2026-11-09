# Environments in camp

Two readers, two sections: to make a project's containers show up in `camp env`, go to [Adopting the contract](#adopting-the-contract).
To prepare a machine so `camp env` can read it, go to [Preparing an environment host](#preparing-an-environment-host).

## What `camp env` does with an environment

An environment is a running (or stopped) Docker container that a project has labelled so camp can find it. `camp env ls` lists
every environment on this machine and on every host you opt in; `camp env attach` puts a remote environment's ports on this
machine's localhost. Camp reads the environments by running one `docker ps` command on each machine and believing the reply only
if every labelled container follows the contract below. This guide is for the people who write that contract into a project, and
for the person who sets up a machine to be read.

## Adopting the contract

### The contract

A container is an environment when all of these hold:

- It carries the label `camp.env.group`, whose value is the group (the project).
- It carries the label `camp.env.name`, whose value is the environment's name.
- Its container name is `<group>-env-<name>`, for example `acme-env-main`.
- Group and name each match `[a-z0-9][a-z0-9-]*`: lowercase letters, digits and hyphens, not starting with a hyphen.

A container that carries both labels under any other name is skipped, which is how a project says which of its labelled
containers is the environment and which are helpers beside it. A skipped container is still held to the port rule below.

Published ports:

- Every port is published on `127.0.0.1`, over TCP, never on all interfaces, an IPv6 address or UDP, and never as a range.
- Both the host port and the container port are in 1024-65535. A service that listens on 80 or 443 inside its container must
  move its listener to a port of 1024 or more; changing only the `-p` flag is not enough.
- Each container port is published once.
- The container port is the *standard port*: the port an attachment binds on the attaching machine. `-p 127.0.0.1:18080:8080`
  is reached on the attaching machine at `localhost:8080`.
- A stopped container publishes nothing, and that is fine.

One defect on any labelled container, a skipped one included, makes the whole machine unreadable: camp lists no environment from
that machine until it is fixed.

### Applying the contract to a container

Put the labels on the container, never on the image. A label on an image is inherited by every container run from it, so every
one of them would claim to be an environment. A container that already exists gains labels only by being rebuilt (removed and run
again).

A container that follows the contract:

```sh camp-env-example
docker run -d --name acme-env-main \
  --label camp.env.group=acme \
  --label camp.env.name=main \
  -p 127.0.0.1:18173:5173 \
  -p 127.0.0.1:18080:8080 \
  alpine sleep 3600
```

Replace `alpine sleep 3600` with your image and command. Keep the example minimal: no `--privileged`, no mount of
`/var/run/docker.sock`, and no port published outside `127.0.0.1`.

Not allowed: a port published on all interfaces (no address in front of the host port).

```sh camp-env-counter-example
docker run -d --name acme-env-main \
  --label camp.env.group=acme \
  --label camp.env.name=main \
  -p 18080:8080 \
  alpine sleep 3600
```

Not allowed: a container port below 1024.

```sh camp-env-counter-example
docker run -d --name acme-env-main \
  --label camp.env.group=acme \
  --label camp.env.name=main \
  -p 127.0.0.1:18080:80 \
  alpine sleep 3600
```

Rules, each with an example:

A labelled container under another name is skipped. This one is not an environment, and camp lists `no environments` for it:

```sh camp-env-wrong-name
docker run -d --name acme-helper \
  --label camp.env.group=acme \
  --label camp.env.name=main \
  -p 127.0.0.1:18090:9090 \
  alpine sleep 3600
```

A skipped container is still held to the port rule. This helper makes the whole machine unreadable:

```sh camp-env-rule-helper
docker run -d --name acme-helper \
  --label camp.env.group=acme \
  --label camp.env.name=main \
  -p 18090:9090 \
  alpine sleep 3600
```

A container port published twice makes the whole machine unreadable:

```sh camp-env-rule-duplicate
docker run -d --name acme-env-main \
  --label camp.env.group=acme \
  --label camp.env.name=main \
  -p 127.0.0.1:18080:8080 \
  -p 127.0.0.1:18081:8080 \
  alpine sleep 3600
```

A stopped container publishes nothing. This one is created but not started, so it lists as `stopped` with no ports:

```sh camp-env-rule-stopped
docker create --name acme-env-idle \
  --label camp.env.group=acme \
  --label camp.env.name=idle \
  -p 127.0.0.1:18080:8080 \
  alpine sleep 3600
```

### Choosing host ports

The project chooses its own host ports; camp carries no port scheme. LEVR is one example: it gives each environment a numbered
slot and publishes each standard port at `10000 + 1000 × slot + (standard port − 8000)`, so slot 1 reaches standard port 8080 at
host port 11080. Any scheme works if it keeps two environments on one machine from colliding.

### Checking an adoption

Camp reads a machine with exactly this command (on a remote host it is sent over ssh as this one string):

```sh camp-env-inventory
docker ps -a --filter label=camp.env.group --filter label=camp.env.name --format 'table {{json .Names}}|{{json .State}}|{{json (.Label "camp.env.group")}}|{{json (.Label "camp.env.name")}}|{{json .Ports}}'
```

Run it on the machine to see what camp will see, then run `camp env ls local` to see how camp reads it.

To probe a remote host before you opt it in for real, use a scratch config directory and a throwaway container, then remove it so
no phantom environment stays in anyone's listing. Replace `builder.example.com` with the host's ssh name and `camp_bin` with the
path of camp on it:

```sh
mkdir -p ~/.cache/camp-probe
printf 'env_hosts = ["builder"]\n\n[hosts.builder]\nssh = "builder.example.com"\ncamp_bin = "/path/to/camp"\n' > ~/.cache/camp-probe/hosts.toml
ssh builder.example.com docker run -d --name probe-env-check --label camp.env.group=probe --label camp.env.name=check -p 127.0.0.1:28080:8080 alpine sleep 3600
env CAMP_CONFIG_DIR=$HOME/.cache/camp-probe camp env ls builder
ssh builder.example.com docker rm -f probe-env-check
```

The listing should show `probe  check  running` under `builder`. The last line removes the probe container.

### If you see something else

`camp env ls` prints `unreadable: <reason>` for a machine it will not believe. The line number counts lines of the output of
the `docker ps` command above, header included (the header is line 1). One labelled container's bad port makes the whole machine
unreadable, even when it is a helper that is skipped.

| If you see | It means | Fix |
| --- | --- | --- |
| `reply does not start with the expected header line` | the command did not print its header: Docker printed an error, or something else answered | run the `docker ps` command above on that machine and read what it prints |
| `line <N>: not five JSON strings separated by '\|'` | line N is not a record this command produced | run the command on that machine; a wrapper or older Docker that rewrites its output will not be believed |
| `line <N>: group is not a valid identifier` | the container's `camp.env.group` label is not lowercase letters, digits and hyphens | fix the label and rebuild the container |
| `line <N>: name is not a valid identifier` | the container's `camp.env.name` label is not lowercase letters, digits and hyphens | fix the label and rebuild the container |
| `line <N>: ports are not distinct loopback tcp mappings within 1024-65535` | a port is published on all interfaces, IPv6, UDP or a range, outside 1024-65535, or a container port twice | publish each port as `-p 127.0.0.1:<host>:<container>` once, both in 1024-65535, and rebuild; check helpers too |
| `no environments` | nothing on that machine passed the contract | check that the container carries both labels and is named `<group>-env-<name>` |

## Preparing an environment host

A machine is read by running `docker ps` on it as the ssh user. Opting a host in is a trust decision: Docker access is
root-equivalent on that host, so opt in only hosts whose ssh user you would trust with a root shell there.

Do these in order on a machine that should be read:

1. Install Trailhead on the host. Camp's all-hosts verbs reach every declared host, and the host is declared as a camp host
   running Trailhead.
2. Declare it in `~/.config/camp/hosts.toml` as `[hosts.<name>]` with `ssh` and `camp_bin`. `camp_bin` is usually required:
   camp is often not on a non-interactive ssh PATH.
3. Set `self_name` on each machine, so every machine knows its own name. A machine named in `env_hosts` by its own `self_name` is
   read locally, never over ssh.
4. Set up ssh keys. Camp connects with `BatchMode=yes`, so it never prompts for a password or passphrase; load the key into
   an agent or use a key without a passphrase.
5. Verify the host key once, out of band, before the first connect. Camp pins `StrictHostKeyChecking=yes`, so the key must
   already be in `known_hosts`. On the host, print its fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.
   On your machine run `ssh <host>` once and compare the fingerprint ssh shows against that one before you answer `yes`. Do not
   work around the pin with `accept-new`, `no`, or a blind `ssh-keyscan` piped into `known_hosts`.
6. On a Mac, put `docker` on the PATH of a non-interactive ssh command. Check with `ssh <host> command -v docker`. If it prints
   nothing, add Homebrew's directory for the host's login shell: in fish run `fish_add_path /opt/homebrew/bin` once on the
   host, and in zsh add `export PATH=/opt/homebrew/bin:$PATH` to `~/.zshenv`.
7. Add the host to `env_hosts`, the top-level list `camp env ls` reads. Each entry is a declared `[hosts.<name>]` or this
   machine's `self_name`.

The hosts file, with this machine opted in beside a remote one:

```toml camp-env-hosts
self_name = "laptop"
env_hosts = ["laptop", "builder"]

[hosts.builder]
ssh = "builder.example.com"
camp_bin = "/Users/you/.local/state/trailhead/bin/camp"
```

### A Mac host

Docker Desktop runs inside a logged-in user's session, so after a reboot a Mac host serves no environments until someone logs
in. Automatic login fixes that, with a cost: it is unavailable while FileVault is on (Apple: when FileVault is turned on, manual
login is required, see [Apple's login-settings article](https://support.apple.com/en-us/102316)), and it weakens physical
security, since anyone with the machine in hand reaches the logged-in session. It fits a dedicated, physically controlled host.
The alternative is to log in once at the machine after each reboot.

## Using environments

[`camp env ls`](../README.md#listing-environments) lists what you have opted in, and
[`camp env attach`](../README.md#attaching-an-environment) puts one on your localhost. Attach assumes a single-user machine:
the forward or relay it starts listens on 127.0.0.1, so any local user on that machine can connect through it. Do not attach on a
machine other people log in to.
