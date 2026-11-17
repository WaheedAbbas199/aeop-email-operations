"""Repeatable evaluation of the classification layer on a SMALL SYNTHETIC dataset (evaluation/dataset.jsonl).
Run:  python -m evaluation.run_eval
The dataset is tiny and written by the developers: results are a regression signal, NOT evidence of production accuracy.
Extend it with anonymized real emails before drawing conclusions."""
import json
from pathlib import Path

from app import intelligence as I
from app import llm

PATH = Path(__file__).parent / "dataset.jsonl"


def evaluate(path=PATH, threshold=0.6):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    ok_wf = ok_cat = 0
    inj_tp = inj_fn = inj_fp = 0
    review = 0
    per = {}
    wrong = []
    for r in rows:
        res, ents, engine, usage, flags = I.classify_email(r["subject"], r["body"], [(a, "") for a in r["attachments"]], threshold)
        wf_ok = res.recommended_workflow == r["workflow"]
        ok_wf += wf_ok
        ok_cat += res.category == r["category"]
        review += res.recommended_workflow == "manual_review"
        if r["injection"]:
            inj_tp += bool(flags)
            inj_fn += not flags
        else:
            inj_fp += bool(flags)
        p = per.setdefault(r["workflow"], {"tp": 0, "fp": 0, "fn": 0})
        if wf_ok:
            p["tp"] += 1
        else:
            p["fn"] += 1
            per.setdefault(res.recommended_workflow, {"tp": 0, "fp": 0, "fn": 0})["fp"] += 1
            wrong.append({"subject": r["subject"], "expected": r["workflow"], "got": res.recommended_workflow, "confidence": res.confidence})
    n = len(rows)
    f = lambda a, b: round(a / b, 3) if b else None
    return {"n": n, "engine": llm.engine_name(), "prompt_version": I.PROMPT_VERSION,
            "accuracy": f(ok_wf, n), "category_accuracy": f(ok_cat, n), "manual_review_rate": f(review, n),
            "injection_detection_rate": f(inj_tp, inj_tp + inj_fn), "injection_false_positives": inj_fp,
            "per_workflow": {k: {"precision": f(v["tp"], v["tp"] + v["fp"]), "recall": f(v["tp"], v["tp"] + v["fn"])} for k, v in per.items()},
            "misclassified": wrong,
            "note": "Synthetic 24-example set; perfect scores on a small self-written set only show the heuristics match the examples they were written against; they are not evidence of production accuracy."}


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2))
