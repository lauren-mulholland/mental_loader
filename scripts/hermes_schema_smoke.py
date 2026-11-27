"""Check real native delegation validation, including rejection; no model calls."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".local/hermes-runtime"))


def main():
    # Hermes falls back to JSON parsing when this optional package is absent.
    import jsonschema  # noqa: F401
    from tools.delegation_output_schema import validate_output

    schema = json.loads(
        (ROOT / "skills/household-meals/references/output.schema.json").read_text()
    )
    valid = {"owner": "meals", "status": "proposal", "meals": [],
             "questions": [], "requires_acceptance": True}
    assert validate_output(json.dumps(valid), schema) == (True, [])
    invalid = dict(valid, owner="supplies", requires_acceptance=False)
    accepted, errors = validate_output(json.dumps(invalid), schema)
    assert not accepted and errors, "Native validator accepted schema-invalid JSON"
    print("PASS: native delegation validator accepts matching JSON and rejects wrong "
          "owner/acceptance fields. No model calls.")


if __name__ == "__main__":
    main()
