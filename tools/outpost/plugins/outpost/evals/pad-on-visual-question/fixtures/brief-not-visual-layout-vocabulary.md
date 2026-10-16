Our deploy configuration has outgrown its single file, and I want to decide how
to lay it out on disk: one big YAML with a section per environment, a separate
file per environment under conf.d/, or a shared base file plus a small override
file for each environment. What I care about is clean diffs and how often two
people collide in a merge. Help me choose a structure.
