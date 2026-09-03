"""Shared payload schema and the boundary validator every write endpoint uses."""


class ValidationError(Exception):
    pass


SCHEMAS = {
    "order": {"customer_id": int, "total_cents": int},
    "invoice": {"account_id": int, "amount_cents": int},
    "export": {"account_id": int, "format": str},
    "refund": {"order_id": int, "amount_cents": int},
}


def validate(payload, schema_name):
    """Reject a payload that does not match the named schema."""
    schema = SCHEMAS[schema_name]
    for field, kind in schema.items():
        if field not in payload:
            raise ValidationError(f"missing field: {field}")
        if not isinstance(payload[field], kind):
            raise ValidationError(f"wrong type for {field}")
    return payload
