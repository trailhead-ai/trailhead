# Task

We finished the latency investigation on the glasswing ingest path. Write it up
as an HTML page: the three findings below, the measurements as simple tables,
and the recommendation at the end. The rest of the team needs to read it.

Findings:
1. p99 latency tripled after the Marlowe cutover, concentrated in the eu-west shard.
2. The regression tracks batch size, not request volume.
3. Reverting the batch ceiling to 64 restores p99 to pre-cutover levels.

Measurements: eu-west p99 840ms (was 270ms); us-east p99 290ms (was 265ms).

Recommendation: pin the batch ceiling at 64 and re-measure before raising it.
