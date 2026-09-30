#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, json, os, re, sys
[OUTPUT]: 提供理解测验自动评分命令——按 rubric 给 docs_only/code_only 答案打分并计算 token 比
[POS]: fugue-docs 评测包-理解成本评分器(度量只读索引是否能低 token 获得接近读代码的理解质量)
[PROTOCOL]: rubric 或输出结构变更时同步更新 evals/comprehension.md、evals/FOLDER_INDEX.md
"""

import argparse
import json
import os
import re
import sys

WS = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SPEC = os.path.join(WS, "comprehension_fixture_b.json")


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "").lower())


def answer_map(raw):
    answers = raw.get("answers", raw)
    if isinstance(answers, list):
        return {str(i + 1): str(v) for i, v in enumerate(answers)}
    return {str(k): str(v) for k, v in answers.items()}


def has_any(text, keywords):
    haystack = normalize(text)
    return any(normalize(k) in haystack for k in keywords)


def has_all(text, keywords):
    haystack = normalize(text)
    return all(normalize(k) in haystack for k in keywords)


def point_passed(answer, point):
    ok = True
    if point.get("any"):
        ok = ok and has_any(answer, point["any"])
    if point.get("all"):
        ok = ok and has_all(answer, point["all"])
    return ok


def grade_question(answer, question):
    earned = 0.0
    details = []
    for point in question.get("rubric", []):
        passed = point_passed(answer, point)
        weight = float(point.get("weight", 0))
        if passed:
            earned += weight
        details.append({
            "label": point.get("label", ""),
            "weight": weight,
            "passed": passed,
        })
    max_score = sum(float(p.get("weight", 0)) for p in question.get("rubric", []))
    return {
        "id": str(question.get("id")),
        "question": question.get("question", ""),
        "score": round(earned, 4),
        "max_score": round(max_score, 4),
        "details": details,
    }


def grade_run(run, spec):
    answers = answer_map(run)
    questions = []
    for q in spec.get("questions", []):
        questions.append(grade_question(answers.get(str(q.get("id")), ""), q))
    score = sum(q["score"] for q in questions)
    max_score = sum(q["max_score"] for q in questions)
    token_count = run.get("token_count", run.get("tokens"))
    if token_count is not None and (isinstance(token_count, bool)
                                    or not isinstance(token_count, int) or token_count <= 0):
        raise ValueError("token_count must be a positive integer or null")
    review = run.get("quality_review", {})
    if not isinstance(review, dict):
        raise ValueError("quality_review must be an object")
    return {
        "condition": run.get("condition", "run"),
        "token_count": token_count,
        "quality_review": review,
        "score": round(score, 4),
        "max_score": round(max_score, 4),
        "score_rate": round(score / max_score, 4) if max_score else 0.0,
        "questions": questions,
    }


def condition_key(name):
    n = normalize(name).replace("-", "_")
    if n in ("docs_only", "docs", "document_only", "condition_a", "a"):
        return "docs_only"
    if n in ("code_only", "code", "source_only", "condition_b", "b"):
        return "code_only"
    return n


def compare_runs(results, spec):
    by_name = {condition_key(r["condition"]): r for r in results}
    if len(by_name) != len(results):
        raise ValueError("duplicate comparison conditions; pair runs explicitly")
    docs = by_name.get("docs_only")
    code = by_name.get("code_only")
    if not docs or not code:
        return None
    thresholds = spec.get("thresholds", {})
    min_score_ratio = float(thresholds.get("score_ratio", 0.85))
    max_token_ratio = float(thresholds.get("token_ratio", 0.40))
    score_ratio = docs["score_rate"] / code["score_rate"] if code["score_rate"] else None
    token_ratio = (float(docs["token_count"]) / code["token_count"]
                   if docs["token_count"] and code["token_count"] else None)
    min_absolute_score = float(thresholds.get("absolute_score", 0.85))
    comparable = score_ratio is not None and token_ratio is not None
    proxy_healthy = (score_ratio >= min_score_ratio and token_ratio <= max_token_ratio
                     and min(docs["score_rate"], code["score_rate"]) >= min_absolute_score
                     if comparable else None)
    reviewed = all(r.get("quality_review", {}).get("passed") is True
                   and r["quality_review"].get("evidence") for r in (docs, code))
    return {
        "docs_vs_code_score_ratio": round(score_ratio, 4) if score_ratio is not None else None,
        "docs_vs_code_token_ratio": round(token_ratio, 4) if token_ratio is not None else None,
        "score_threshold": min_score_ratio,
        "token_threshold": max_token_ratio,
        "absolute_score_threshold": min_absolute_score,
        "proxy_healthy": proxy_healthy,
        "review_required": not reviewed,
        "healthy": proxy_healthy if reviewed else None,
        "status": "missing_measurements" if not comparable else ("reviewed" if reviewed else "needs_quality_review"),
    }


def load_runs(path):
    data = read_json(path)
    if isinstance(data, list):
        return data
    if "runs" in data:
        return data["runs"]
    return [data]


def main():
    parser = argparse.ArgumentParser(description="GEB 理解测验评分器")
    parser.add_argument("answers", help="答案 JSON:包含 runs 或单个 run")
    parser.add_argument("--spec", default=DEFAULT_SPEC,
                        help="rubric JSON(默认 evals/comprehension_fixture_b.json)")
    parser.add_argument("--out", help="把评分结果写入文件;默认输出到 stdout")
    args = parser.parse_args()

    spec = read_json(args.spec)
    runs = load_runs(args.answers)
    try:
        results = [grade_run(run, spec) for run in runs]
        report = {
            "spec": spec.get("name", os.path.basename(args.spec)),
            "results": results,
            "comparison": compare_runs(results, spec),
        }
    except ValueError as error:
        parser.error(str(error))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
