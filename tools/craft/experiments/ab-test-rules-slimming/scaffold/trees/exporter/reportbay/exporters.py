import json

from .registry import register


def _columns(rows):
    return sorted(rows[0]) if rows else []


@register("csv")
def to_csv(rows):
    cols = _columns(rows)
    lines = [",".join(cols)]
    lines += [",".join(str(row.get(c, "")) for c in cols) for row in rows]
    return "\n".join(lines) + "\n"


@register("json")
def to_json(rows):
    return json.dumps(rows, sort_keys=True)


@register("tsv")
def to_tsv(rows):
    cols = _columns(rows)

    def cell(value):
        return str(value).replace("\t", " ").replace("\n", " ")

    lines = ["\t".join(cols)]
    lines += ["\t".join(cell(row.get(c, "")) for c in cols) for row in rows]
    return "\n".join(lines) + "\n"
