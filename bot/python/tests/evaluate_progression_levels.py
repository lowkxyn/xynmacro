"""Evaluate all 191 supplied crops against independently recorded visual labels.

Usage: python tests/evaluate_progression_levels.py PATH_TO_LEVEL_FRAMES
Only reads saved images. Never imports the macro controller or captures input.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import training_ui as ui


def evaluate(source):
    manifest = json.loads((Path(__file__).parent / "fixtures/progression_2026_10/ground_truth.json").read_text())
    counts = defaultdict(lambda: dict(labels=0, correct=0, unknown=0, non_labels=0,
                                      rejected=0, wrong=0, completion_changes=0))
    for name, entry in manifest.items():
        path = source / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Evidence changed: {name}")
        image = cv2.imread(str(path))
        actual = ui.read_progression_level(image)
        row = counts[name.split("_")[0]]
        if entry["level"] is None:
            row["non_labels"] += 1
            row["rejected"] += actual is None
            row["wrong"] += actual is not None
        else:
            row["labels"] += 1
            row["correct"] += actual == (entry["level"], 20)
            row["unknown"] += actual is None
            row["wrong"] += actual is not None and actual != (entry["level"], 20)
        row["completion_changes"] += (
            ui.progression_complete(ui.progression_numbers(image)) is not entry["completion"])
    print("Run | Correct labels | Unknown labels | Non-labels rejected | Wrong | Completion changes")
    for run, row in sorted(counts.items()):
        print(f"{run} | {row['correct']}/{row['labels']} | {row['unknown']} | "
              f"{row['rejected']}/{row['non_labels']} | {row['wrong']} | {row['completion_changes']}")
    return counts


if __name__ == "__main__":
    results = evaluate(Path(sys.argv[1]))
    if any(row["wrong"] or row["completion_changes"] for row in results.values()):
        raise SystemExit(1)
