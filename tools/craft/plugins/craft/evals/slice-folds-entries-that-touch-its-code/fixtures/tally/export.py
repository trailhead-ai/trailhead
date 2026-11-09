import json


def export_counts(counts, out_path):
    with open(out_path, "w") as handle:
        json.dump(counts, handle)
