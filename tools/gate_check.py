#!/usr/bin/env python3
"""핸드오프 게이트 기록을 만들고 검사한다. 표준 라이브러리만 사용한다.

사용법
  python tools/gate_check.py new G3 --lesson <수업명> --sender lesson-plan \
      --receiver worksheets --spec tmp/<수업명>/spec.md --spec-version v3 [--input 파일 ...]
  python tools/gate_check.py check tmp/<수업명>/gates/G3_worksheets_20261014-101500.json
  python tools/gate_check.py hash 파일 [파일 ...]

check 종료 코드
  0  ready              다음 담당에게 넘긴다
  10 conditional_ready  기록한 조건과 함께 넘긴다
  20 revision_required  넘기지 않고 보낸 쪽에 반려한다
  2  기록 형식 오류      판정을 사용하지 않고 기록을 고친다
  3  입력 변경(stale)    명세·입력 파일이 바뀌었으므로 다시 검사한다
  4  반려 한도 초과      자동 반복을 멈추고 총괄이 사용자에게 보고한다
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATES_FILE = ROOT / "gates" / "gates.json"
KST = timezone(timedelta(hours=9))

EXIT = {"ready": 0, "conditional_ready": 10, "revision_required": 20}
EXIT_INVALID, EXIT_STALE, EXIT_ESCALATE = 2, 3, 4


def load_gates() -> dict:
    return json.loads(GATES_FILE.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve(base: Path, p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else base / q


def compute_decision(gate: dict, checks: dict[str, dict]) -> tuple[str, list[str]]:
    """게이트 정의와 항목 판정으로 결과를 계산한다. 자기 판정을 바꿔 통과시키지 못하게 기계적으로 정한다."""
    allow_cond = gate.get("allow_conditional", False)
    hard, soft = [], []
    for c in gate["checks"]:
        st = checks[c["id"]]["status"]
        if c["level"] == "must":
            if st in ("block", "not_assessed"):
                hard.append(f"{c['id']} {c['title']}: {st}")
            elif st == "warning":
                (soft if allow_cond else hard).append(f"{c['id']} {c['title']}: warning")
        elif st != "pass":
            soft.append(f"{c['id']} {c['title']}: {st}")
    if hard:
        return "revision_required", hard
    if soft and allow_cond:
        return "conditional_ready", soft
    return "ready", soft  # 조건부를 허용하지 않는 게이트의 should 항목은 conditions에 남기고 통과


def cmd_hash(args) -> int:
    for p in args.files:
        print(f"{sha256(Path(p))}  {p}")
    return 0


def cmd_new(args) -> int:
    gates = load_gates()
    gate = gates["gates"].get(args.gate)
    if not gate:
        print(f"알 수 없는 게이트: {args.gate}", file=sys.stderr)
        return EXIT_INVALID
    base = Path(args.root).resolve() if args.root else Path.cwd()
    now = datetime.now(KST)
    spec = resolve(base, args.spec)
    record = {
        "schema_version": gates["schema_version"],
        "gate": args.gate,
        "gate_name": gate["name"],
        "lesson": args.lesson,
        "checked_at": now.isoformat(timespec="seconds"),
        "sender": args.sender,
        "receiver": args.receiver,
        "judge": args.receiver,
        "spec": {"version": args.spec_version, "path": args.spec,
                 "sha256": sha256(spec) if spec.exists() else None},
        "inputs": [{"path": p, "sha256": sha256(resolve(base, p)) if resolve(base, p).exists() else None}
                   for p in (args.input or [])],
        "checks": [{"id": c["id"], "title": c["title"], "level": c["level"], "question": c["question"],
                    "status": "not_assessed", "reason": "", "location": None} for c in gate["checks"]],
        "decision": "revision_required",
        "conditions": [],
        "revision_requests": [],
        "rejection_count": 0,
        "previous_record": None,
    }
    if gate.get("requires_user_confirmation"):
        record["user_confirmation"] = {"confirmed": False, "quote": "", "at": None}
    out_dir = base / "tmp" / args.lesson / "gates"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.gate}_{args.receiver}_{now:%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out)
    return 0


def cmd_check(args) -> int:
    gates = load_gates()
    path = Path(args.record)
    base = Path(args.root).resolve() if args.root else Path.cwd()
    errors: list[str] = []
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[형식 오류] 기록을 읽을 수 없음: {e}")
        return EXIT_INVALID

    gate = gates["gates"].get(rec.get("gate"))
    if not gate:
        print(f"[형식 오류] 알 수 없는 게이트: {rec.get('gate')}")
        return EXIT_INVALID

    # 1) 판정 주체: 받는 쪽이 판정하고, 보낸 쪽 자기 판정은 인정하지 않는다
    if rec.get("receiver") not in gate["receivers"]:
        errors.append(f"receiver '{rec.get('receiver')}'는 {rec['gate']}의 받는 쪽이 아님 ({gate['receivers']})")
    if rec.get("judge") != rec.get("receiver"):
        errors.append("judge는 receiver와 같아야 함(받는 쪽 재검사 원칙)")
    if rec.get("judge") and rec.get("judge") == rec.get("sender"):
        errors.append("보낸 쪽이 스스로 판정한 기록은 게이트로 인정하지 않음")

    # 2) 항목 완전성
    allowed = set(gates["statuses"])
    by_id: dict[str, dict] = {}
    for c in rec.get("checks", []):
        cid = c.get("id")
        if cid in by_id:
            errors.append(f"{cid} 중복")
        by_id[cid] = c
        if c.get("status") not in allowed:
            errors.append(f"{cid} 상태값 오류: {c.get('status')}")
        elif c.get("status") != "pass" and not str(c.get("reason", "")).strip():
            errors.append(f"{cid}: pass가 아니면 reason 필수")
    defined = {c["id"] for c in gate["checks"]}
    missing, unknown = defined - by_id.keys(), by_id.keys() - defined
    if missing:
        errors.append(f"누락 항목: {sorted(missing)}")
    if unknown:
        errors.append(f"정의되지 않은 항목: {sorted(unknown)}")

    # 3) 사용자 확인(G3): 침묵·답변 부재는 확인이 아니다
    if gate.get("requires_user_confirmation") and not missing:
        uc = rec.get("user_confirmation") or {}
        ok = uc.get("confirmed") is True and str(uc.get("quote", "")).strip()
        first = gate["checks"][0]["id"]
        if not ok and by_id[first].get("status") == "pass":
            errors.append(f"{first}가 pass인데 user_confirmation(confirmed=true, quote)이 없음")

    if errors:
        print("[형식 오류] 판정을 사용하지 말고 기록을 고칠 것")
        for e in errors:
            print(" -", e)
        return EXIT_INVALID

    # 4) 입력 고정: 명세·입력 파일이 바뀌면 기존 판정을 쓰지 않는다
    stale = []
    pinned = [rec.get("spec") or {}] + list(rec.get("inputs") or [])
    if not (rec.get("spec") or {}).get("sha256"):
        stale.append("spec.sha256 없음(명세가 동결되지 않음)")
    for item in pinned:
        if not item.get("path"):
            continue
        f = resolve(base, item["path"])
        if not f.exists():
            stale.append(f"파일 없음: {item['path']}")
        elif item.get("sha256") and sha256(f) != item["sha256"]:
            stale.append(f"해시 불일치(파일 변경됨): {item['path']}")
    if stale:
        print("[입력 변경] 현재 파일로 다시 검사할 것")
        for s in stale:
            print(" -", s)
        return EXIT_STALE

    # 5) 결과 재계산과 기록 일치
    decision, reasons = compute_decision(gate, by_id)
    if rec.get("decision") != decision:
        print(f"[형식 오류] 기록된 decision '{rec.get('decision')}' ≠ 규칙 계산 결과 '{decision}'")
        for r in reasons:
            print(" -", r)
        return EXIT_INVALID
    if decision == "conditional_ready" and not rec.get("conditions"):
        print("[형식 오류] conditional_ready에는 conditions(넘길 조건·제한)가 필요")
        return EXIT_INVALID
    if decision == "revision_required" and not rec.get("revision_requests"):
        print("[형식 오류] revision_required에는 revision_requests(위치·수정안)가 필요")
        return EXIT_INVALID

    # 6) 반려 한도
    limit = gates["max_rejections_per_spec_version"]
    if decision == "revision_required" and int(rec.get("rejection_count", 0)) >= limit:
        print(f"[반려 한도 초과] 같은 명세 버전에서 {limit}회 반려됨. 자동 반복을 멈추고 총괄이 사용자에게 보고할 것")
        for r in reasons:
            print(" -", r)
        return EXIT_ESCALATE

    label = {"ready": "통과", "conditional_ready": "조건부 통과", "revision_required": "반려"}[decision]
    print(f"[{rec['gate']} {gate['name']}] {rec['sender']} → {rec['receiver']}: {label} ({decision})")
    for r in reasons:
        print(" -", r)
    return EXIT[decision]


def main() -> int:
    ap = argparse.ArgumentParser(description="핸드오프 게이트 기록 생성·검사")
    ap.add_argument("--root", help="경로 기준 폴더(기본: 현재 폴더)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="빈 게이트 기록 생성")
    n.add_argument("gate")
    n.add_argument("--lesson", required=True)
    n.add_argument("--sender", required=True)
    n.add_argument("--receiver", required=True)
    n.add_argument("--spec", required=True)
    n.add_argument("--spec-version", required=True)
    n.add_argument("--input", action="append")
    n.set_defaults(func=cmd_new)

    c = sub.add_parser("check", help="게이트 기록 검사")
    c.add_argument("record")
    c.set_defaults(func=cmd_check)

    h = sub.add_parser("hash", help="SHA-256 출력")
    h.add_argument("files", nargs="+")
    h.set_defaults(func=cmd_hash)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
