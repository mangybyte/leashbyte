"""Write the public OpenAPI schema without starting a service."""

import json
from pathlib import Path

from leashbyte.config import Settings
from leashbyte.main import create_app

app = create_app(Settings(api_key="schema-generation-only"))
output = Path(__file__).resolve().parent.parent / "openapi.json"
output.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
print(f"Wrote {output.name}")
