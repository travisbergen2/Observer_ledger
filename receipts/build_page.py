#!/usr/bin/env python3
"""build_page.py — inject the twin's JSON and the Backtrader receipt into the page template.

    python3 receipts/observer_twin.py --selftest   # writes receipts/observer_twin.json
    python3 receipts/build_page.py                 # writes ../index.html from receipts/index.template.html

The page embeds two objects: TWIN (observer_twin.json, complete) and BT (the broker-independent
parts of backtrader_receipt.json: decisions, final states, summary, hashes). Everything else on
the page is recomputed in the browser and compared with these.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main() -> None:
    twin = json.loads((HERE / "observer_twin.json").read_text())
    bt_full = json.loads((HERE / "backtrader_receipt.json").read_text())
    bt = {k: bt_full[k] for k in ("generated", "backtrader_version", "python", "scaffold_sha256", "smoke_sha256",
                                 "defaults", "result", "bars", "decisions", "final_states", "first_halt_bar",
                                 "detections", "action_counts", "transcript_first_commit")}
    template = (HERE / "index.template.html").read_text()
    for token in ("__TWIN_JSON__", "__BT_JSON__", "__BUILD_DATE__"):
        assert template.count(token) == 1, token
    page = (template.replace("__TWIN_JSON__", json.dumps(twin, separators=(",", ":")))
                    .replace("__BT_JSON__", json.dumps(bt, separators=(",", ":")))
                    .replace("__BUILD_DATE__", date.today().isoformat()))
    (ROOT / "index.html").write_text(page)
    print(f"wrote {ROOT / 'index.html'} ({len(page):,} bytes; twin {len(json.dumps(twin)):,} B, receipt {len(json.dumps(bt)):,} B)")


if __name__ == "__main__":
    main()
