import json, re, subprocess, sys
here = sys.argv[1]
base = [here + "/py-home/bin/python3", here + "/lore-plugin/cli/lore"]
show = subprocess.run(base + ["record", "show", "task/count-words", "--json"], capture_output=True, text=True, check=True)
body = json.loads(show.stdout)["body"]
i = body.rindex("## Unattended run")
head, blk = body[:i], body[i:]
blk = re.sub(r"(\*\*Rituals:\*\*[^\n]*\n)", r"\1**Outcome:** stuck\n", blk, count=1)
blk = re.sub(r"\*\*Checkpoint:\*\*[^\n]*", "**Checkpoint:** taken over by a resumed run", blk, count=1)
subprocess.run(base + ["record", "update", "task/count-words"], input=head + blk, text=True, capture_output=True, check=True)
