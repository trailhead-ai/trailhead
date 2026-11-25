---
allowed_tools: [Read, Write, Edit, Glob, Grep]
max_turns: 30
timeout_seconds: 480
---
This directory holds the `addup` package, a small command that prints the sum of the numbers it is given (`addup/cli.py`). Add a `--precision N` option: when it is given, the printed total shows exactly N digits after the decimal point; without it the output stays as it is. Run the tests with `./run-tests`. It takes no arguments, nothing else here will run code, and each Bash call must be that single command. Say in your final message what you changed and what you ran.
