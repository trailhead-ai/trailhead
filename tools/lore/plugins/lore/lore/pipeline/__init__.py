"""The cross-vault pipeline board — a read-only projection of every configured vault.

Three seams, one direction of flow:

  - :mod:`walk` does all the vault I/O. It reads ``adr``/``spec``/``task``
    sidecars and nothing else, and never raises: every failure comes back as a
    per-file warning or a per-vault error marker.
  - :mod:`derive` is pure. It turns the walk's output into the board's
    lineages and evaluates each record's ``depends-on`` entries, resolving
    every edge and every dependency inside the vault the record came from and
    never across a merged view of them all.
  - :mod:`render` owns both output modes and is the single point at which any
    vault-authored value reaches a stream.

Nothing in this package writes to a vault, touches the index, or claims work.

**Fencing chokepoint.** Content from a ``shared: true`` vault is untrusted
input on its way into an agent's context. Every vault-authored field is
declared in :mod:`render` and passes through the shared fence there — the
``<external-memory>`` wrapper in human mode, XML entity escaping plus an
explicit ``layer`` marker in JSON mode. No other module in this package may
serialize or print a vault-authored value; a test enforces that, so the
chokepoint holds by construction rather than by review.
"""
