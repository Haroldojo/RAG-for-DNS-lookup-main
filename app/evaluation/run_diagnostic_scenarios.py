"""Report the deterministic Phase 8 scenario corpus; network outcomes stay mocked in tests."""

import json
from collections import Counter
from pathlib import Path


def main() -> None:
    path = Path(__file__).parents[2] / "data" / "evaluation" / "diagnostic_scenarios.json"
    scenarios = json.loads(path.read_text(encoding="utf-8"))
    assert len(scenarios) >= 20
    print(
        {
            "scenarios": len(scenarios),
            "intents": dict(Counter(item["intent"] for item in scenarios)),
        }
    )


if __name__ == "__main__":
    main()
