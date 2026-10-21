import json
from pathlib import Path

from bpreviewer.models import Manifest

destination = Path(__file__).resolve().parents[1] / "docs/manifest.schema.json"
destination.write_text(json.dumps(Manifest.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("Updated docs/manifest.schema.json")
