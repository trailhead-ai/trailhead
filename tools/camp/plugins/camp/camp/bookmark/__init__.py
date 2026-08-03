"""camp bookmarks — durable named pointers from a camp workspace to a session.

``store`` owns the global ref-keyed JSON store and its CRUD surface; ``capture``
owns the ``camp bookmark`` command that records the CURRENT session; ``render``
owns ``camp bookmark ls``/``rm``; ``resume`` owns ``camp resume``, which prints
what the shell integration needs to re-enter a bookmarked session.
"""
