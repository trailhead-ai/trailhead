# Install manifest format

The install manifest (`trailhead/install_manifest.toml`) is the suite-level
artifact that pins which repos at which commit SHAs constitute a trailhead
install.  It is distinct from each tool's `capabilities.toml` (the
capability manifest, which describes what a tool can do at runtime) —
see [composition-seam.md](composition-seam.md) for that layer.

## Per-repo entry shape

A manifest entry is one of two kinds — a **remote** entry (fetched and pinned)
or a **local-self** entry (the working tree you run from).

```toml
# Remote entry — pinned + GPG-verified
[[repo]]
name   = "trailhead"                       # unique repo identifier
rev    = "<40-char-lowercase-hex-sha>"     # full commit SHA — see rules below
source = "${registry}/trailhead"           # ${registry}-relative or fully-qualified URL
tools  = ["trailhead", "lore", "camp", "craft"]  # tool set this repo provides

# Local-self entry — installs the working tree, tracking HEAD
[[repo]]
name   = "trailhead"
source = "local"                           # the checkout you're running from
tools  = ["trailhead", "lore", "camp", "craft"]
# no rev: a local-self entry pins nothing (see "Local-self source" below)
```

Each manifest may contain one or more `[[repo]]` entries.  Duplicate `name`
values are rejected at parse time (no last-wins silencing).

## Local-self source (`source = "local"`)

A `source` of the reserved value `"local"` marks a **local-self** entry: the
install uses the working tree `trailhead` is being run from, tracking `HEAD`
rather than a pinned commit.

The pin/verify machinery (40-char SHA + `git verify-commit` against the signing
key) exists for the **supply-chain** case: fetching a *remote* repo at an
audited, signed revision.  A local checkout you already control and can read
gets no authenticity benefit from a blessed-SHA gate, and self-pinning is
**circular** — a commit cannot contain its own post-commit hash, so a
self-referential `rev` is stale the moment it's committed.  `source = "local"`
removes that friction.

Rules for a local-self entry:

- It is honored **only when a `local_root` confinement root is provided** to
  `load_install_manifest` (the same trust boundary as a local-path source — the
  caller establishes what "local" resolves to). Otherwise it is rejected.
- It pins **no `rev`**.  A `rev` on a `"local"` entry is a **parse-time error**
  (not silently ignored), because an ignored `rev` re-introduces exactly the
  staleness foot-gun this source exists to remove.
- It **skips** the remote/allowlist source validation (`"local"` is neither a
  URL nor a filesystem path to confine).
- At verify time it is checked for *"is this a git checkout?"* instead of
  *"HEAD == rev"* (`fetch.verify_present_repo`).  No GPG gate runs (there is no
  fetched commit to authenticate).  Remote entries keep the full SHA + GPG gate.
- `trailhead update` treats it as "changed" only when `HEAD` has moved since the
  last wire, so an untouched tree stays a no-op while a fresh commit re-wires.

The committed `install_manifest.toml` uses a local-self entry for `trailhead`
itself: you install the suite from the checkout you're sitting in.

## Rev rules: full 40-char SHAs only — never tags or symbolic refs

The `rev` field **must** be exactly 40 lowercase hexadecimal characters.

These are **rejected** at parse time with a named `InstallManifestError`:

| Rejected form | Example      | Reason |
|---------------|--------------|--------|
| Tag           | `v1.0`       | §1112: tags can be moved; not a stable anchor |
| Version tag   | `v2.3.4`     | Same as above |
| Short SHA     | `abc1234`    | §1115: not unique across repos |
| 12-char SHA   | `a`×12       | §1115: still not full — rejected |
| `HEAD`        | `HEAD`       | §1115: symbolic, resolves differently per checkout |
| `latest`      | `latest`     | §1115: symbolic, not reproducible |
| Branch name   | `main`       | Moving target |

This enforcement is structural — it happens before any fetch or verification.

## Source field: `${registry}`-relative or fully-qualified URL

Two forms are accepted:

1. **`${registry}`-relative** — `${registry}/path` is expanded against the
   configured registry base at parse time:
   ```toml
   source = "${registry}/trailhead"
   # with registry = "https://github.com/trailhead-ai"
   # resolves to:  "https://github.com/trailhead-ai/trailhead"
   ```
   If no registry is configured and a source uses `${registry}`, parsing
   fails with:
   ```
   repo 'trailhead': source uses '${registry}' but no registry is configured —
   run 'trailhead config registry <url>' to set one
   ```
   Set the registry with: `trailhead config registry <url>`

2. **Fully-qualified URL** — passed through as-is, for per-repo mirrors:
   ```toml
   source = "https://github.com/myorg/trailhead"   # HTTPS
   source = "git@github.com:myorg/trailhead"        # SSH
   ```

### Source security rules

The resolved source is validated before any git invocation:

- Sources beginning with `--` are rejected (git option injection).
- Sources containing `;`, `|`, `` ` ``, or `$(` are rejected (shell
  metacharacters — the loader never passes source through a shell, but
  rejects them defensively).
- Local filesystem paths are passed through the `_confine` confinement
  primitive against the provided `local_root` (reuses the existing confiner — no new one).
- The reserved value `"local"` is not a source URL/path at all — it selects the
  local-self path (see "Local-self source" above) and bypasses this validation.

## Error conditions

| Condition | Error type | Named detail |
|-----------|-----------|--------------|
| Malformed TOML | `InstallManifestError` | File path + TOML error |
| No `[[repo]]` entries | `InstallManifestError` | File path |
| Missing required field (`name`, `rev`, `source`) | `InstallManifestError` | File path + field name + repo name |
| `rev` not a 40-char lowercase hex SHA | `InstallManifestError` | Repo name + field name + rejected value |
| Duplicate repo `name` | `InstallManifestError` | Repo name + file path |
| `${registry}` unresolved (no registry configured) | `InstallManifestError` | Repo name + instruction to set registry |
| Source with `--` prefix or shell metacharacters | `InstallManifestError` | Repo name + source value |
| Local source escaping confinement root | `InstallManifestError` | Repo name + confinement root |
| `source = "local"` with no `local_root` provided | `InstallManifestError` | Repo name + `local_root` hint |
| `source = "local"` carrying a `rev` | `InstallManifestError` | Repo name + "local installs track HEAD" |

## Trust anchor and out-of-band key fingerprint

The install manifest is committed into the repository it describes
(self-referential by design — the manifest pins the repo it ships in).
Integrity is established by git's object graph plus GPG-signed commits:

**Signing key fingerprint: `74AEB40C93C4250A`**

Before installing from a source, import the signing key:

```sh
gpg --recv-keys 74AEB40C93C4250A
```

Integrity verification — including `git verify-commit` of the pinned SHA,
asserting `HEAD == rev`, and the key-not-imported hard-fail — is implemented
in `trailhead/fetch.py`.  This document records the key fingerprint
as the out-of-band trust anchor per §1113.

This trust anchor applies to **remote** entries.  A **local-self** entry
(`source = "local"`) is the checkout you already control: it is not fetched
and has no pinned commit to authenticate, so the SHA + GPG gate does not apply —
verification confirms only that the path is a git checkout.

_Integrity verification details: see `trailhead/fetch.py`._
