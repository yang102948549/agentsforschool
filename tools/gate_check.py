#!/usr/bin/env python3
"""핸드오프 게이트 기록을 만들고 검사한다. 표준 라이브러리만 사용한다.

사용법
  python tools/gate_check.py new G3 --lesson <수업명> --sender lesson-plan \
      --receiver worksheets --spec tmp/<수업명>/spec.md --spec-version v3 \
      [--input 파일 ...] [--previous 직전기록.json]
  python tools/gate_check.py check tmp/<수업명>/gates/G3_lesson-plan_to_worksheets_20261014-101500.json
  python tools/gate_check.py verify --lesson <수업명> --through G5 [--materials worksheets,canva-slides] [--skip G1,G2]
  python tools/gate_check.py docs [--write | --check]
  python tools/gate_check.py hash 파일 [파일 ...]

check 종료 코드
  0  ready              다음 담당에게 넘긴다
  10 conditional_ready  기록한 조건과 함께 넘긴다
  20 revision_required  넘기지 않고 보낸 쪽에 반려한다
  2  기록 형식 오류      판정을 사용하지 않고 기록을 고친다
  3  입력 변경(stale)    명세·입력 파일이 바뀌었으므로 다시 검사한다
  4  반려 한도 초과      자동 반복을 멈추고 총괄이 사용자에게 보고한다

verify 종료 코드: 0 기록 연쇄 정상, 5 연쇄 문제(기록 누락·미통과·명세 해시 불일치)

이 스크립트가 확인하는 것은 기록의 형식, 규칙 계산, 파일 해시, 기록 연쇄이다.
각 항목의 pass가 실제 사실인지, 사용자 확인 인용이 실제 대화에 있는지는 확인할 수 없다.
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
EXIT_INVALID, EXIT_STALE, EXIT_ESCALATE, EXIT_CHAIN = 2, 3, 4, 5
DOC_START, DOC_END = "<!-- gates:tables:start -->", "<!-- gates:tables:end -->"


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


def parse_kst(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.utcoffset() == timedelta(hours=9) else None


def expected_rejections(prev: dict, spec_version: str) -> int:
    """직전 기록에서 이어지는 반려 횟수. 명세 버전이 바뀌면 0부터 센다."""
    if (prev.get("spec") or {}).get("version") != spec_version:
        return 0
    return int(prev.get("rejection_count", 0)) + (1 if prev.get("decision") == "revision_required" else 0)


def load_record(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


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
    if args.sender not in gate["senders"] or args.receiver not in gate["receivers"]:
        print(f"{args.gate}의 보내는 쪽 {gate['senders']}, 받는 쪽 {gate['receivers']}와 맞지 않음", file=sys.stderr)
        return EXIT_INVALID
    base = Path(args.root).resolve() if args.root else Path.cwd()
    now = datetime.now(KST)
    spec = resolve(base, args.spec)
    rejection_count = 0
    if args.previous:
        prev = load_record(resolve(base, args.previous))
        if prev is None:
            print(f"직전 기록을 읽을 수 없음: {args.previous}", file=sys.stderr)
            return EXIT_INVALID
        rejection_count = expected_rejections(prev, args.spec_version)
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
        "rejection_count": rejection_count,
        "previous_record": args.previous,
    }
    if gate.get("requires_user_confirmation"):
        record["user_confirmation"] = {"confirmed": False, "quote": "", "at": None}
    out_dir = base / "tmp" / args.lesson / "gates"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.gate}_{args.sender}_to_{args.receiver}_{now:%Y%m%d-%H%M%S}"
    out, n = out_dir / f"{stem}.json", 1
    while out.exists():  # 기존 기록은 덮어쓰지 않는다
        n += 1
        out = out_dir / f"{stem}-{n}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out)
    return 0


def evaluate(path: Path, base: Path, gates: dict) -> dict:
    """기록 한 건을 검사한다. {'code', 'lines', 'rec', 'decision'}를 돌려준다."""
    def out(code, lines, rec=None, decision=None):
        return {"code": code, "lines": lines, "rec": rec, "decision": decision}

    rec = load_record(path)
    if rec is None:
        return out(EXIT_INVALID, ["[형식 오류] 기록을 읽을 수 없음"])
    gate = gates["gates"].get(rec.get("gate"))
    if not gate:
        return out(EXIT_INVALID, [f"[형식 오류] 알 수 없는 게이트: {rec.get('gate')}"])
    errors: list[str] = []

    # 1) 판정 주체: 받는 쪽이 판정하고, 보낸 쪽 자기 판정은 인정하지 않는다
    if rec.get("sender") not in gate["senders"]:
        errors.append(f"sender '{rec.get('sender')}'는 {rec['gate']}의 보내는 쪽이 아님 ({gate['senders']})")
    if rec.get("receiver") not in gate["receivers"]:
        errors.append(f"receiver '{rec.get('receiver')}'는 {rec['gate']}의 받는 쪽이 아님 ({gate['receivers']})")
    if rec.get("judge") != rec.get("receiver"):
        errors.append("judge는 receiver와 같아야 함(받는 쪽 재검사 원칙)")
    if rec.get("judge") and rec.get("judge") == rec.get("sender"):
        errors.append("보낸 쪽이 스스로 판정한 기록은 게이트로 인정하지 않음")
    if parse_kst(rec.get("checked_at")) is None:
        errors.append("checked_at은 +09:00 시각이어야 함")

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

    # 3) 사용자 확인: 침묵·답변 부재는 확인이 아니다
    if gate.get("requires_user_confirmation") and not missing:
        uc = rec.get("user_confirmation") or {}
        ok = uc.get("confirmed") is True and str(uc.get("quote", "")).strip() and parse_kst(uc.get("at"))
        if not ok and by_id[gate["confirmation_check"]].get("status") == "pass":
            errors.append(f"{gate['confirmation_check']}가 pass인데 user_confirmation(confirmed=true, quote, at +09:00)이 없음")

    # 4) 직전 기록과 반려 횟수의 연쇄
    expected = 0
    prev_path = rec.get("previous_record")
    if prev_path:
        prev = load_record(resolve(base, prev_path))
        if prev is None:
            errors.append(f"previous_record를 읽을 수 없음: {prev_path}")
        else:
            for key in ("gate", "lesson", "sender", "receiver"):
                if prev.get(key) != rec.get(key):
                    errors.append(f"previous_record의 {key}가 다름: {prev.get(key)} ≠ {rec.get(key)}")
            expected = expected_rejections(prev, (rec.get("spec") or {}).get("version"))
    if int(rec.get("rejection_count", 0)) != expected:
        errors.append(f"rejection_count {rec.get('rejection_count')} ≠ 기록 연쇄에서 계산한 값 {expected}"
                      "(반려 뒤 재검사는 --previous로 직전 기록을 연결할 것)")

    if gate.get("requires_inputs") and not rec.get("inputs"):
        errors.append("이 게이트는 검사한 산출물을 inputs에 경로·SHA-256으로 남겨야 함")
    if errors:
        return out(EXIT_INVALID, ["[형식 오류] 판정을 사용하지 말고 기록을 고칠 것"] + [f" - {e}" for e in errors], rec)

    # 5) 입력 고정: 명세·입력 파일이 바뀌면 기존 판정을 쓰지 않는다
    stale = []
    if not (rec.get("spec") or {}).get("sha256"):
        stale.append("spec.sha256 없음(명세가 동결되지 않음)")
    for item in [rec.get("spec") or {}] + list(rec.get("inputs") or []):
        if not item.get("path"):
            continue
        f = resolve(base, item["path"])
        if not f.exists():
            stale.append(f"파일 없음: {item['path']}")
        elif not item.get("sha256"):
            stale.append(f"해시 미기록: {item['path']}")
        elif sha256(f) != item["sha256"]:
            stale.append(f"해시 불일치(파일 변경됨): {item['path']}")
    if stale:
        return out(EXIT_STALE, ["[입력 변경] 현재 파일로 다시 검사할 것"] + [f" - {x}" for x in stale], rec)

    # 6) 결과 재계산과 기록 일치
    decision, reasons = compute_decision(gate, by_id)
    if rec.get("decision") != decision:
        return out(EXIT_INVALID, [f"[형식 오류] 기록된 decision '{rec.get('decision')}' ≠ 규칙 계산 결과 '{decision}'"]
                   + [f" - {r}" for r in reasons], rec)
    if decision != "revision_required" and reasons and not rec.get("conditions"):
        return out(EXIT_INVALID, ["[형식 오류] 미흡한 항목이 있으면 conditions(넘길 조건·제한)에 남겨야 함"]
                   + [f" - {r}" for r in reasons], rec)
    if decision == "revision_required" and not rec.get("revision_requests"):
        return out(EXIT_INVALID, ["[형식 오류] revision_required에는 revision_requests(위치·수정안)가 필요"], rec)

    # 7) 반려 한도
    limit = gates["max_rejections_per_spec_version"]
    if decision == "revision_required" and int(rec.get("rejection_count", 0)) >= limit:
        return out(EXIT_ESCALATE, [f"[반려 한도 초과] 같은 명세 버전에서 {limit}회 반려됨. 자동 반복을 멈추고 총괄이 사용자에게 보고할 것"]
                   + [f" - {r}" for r in reasons], rec, decision)

    label = {"ready": "통과", "conditional_ready": "조건부 통과", "revision_required": "반려"}[decision]
    lines = [f"[{rec['gate']} {gate['name']}] {rec['sender']} → {rec['receiver']}: {label} ({decision})"]
    lines += [f" - {r}" for r in reasons]
    return out(EXIT[decision], lines, rec, decision)


def cmd_check(args) -> int:
    base = Path(args.root).resolve() if args.root else Path.cwd()
    res = evaluate(Path(args.record), base, load_gates())
    print("\n".join(res["lines"]))
    return res["code"]


def latest_records(gates_dir: Path, base: Path, gates: dict) -> dict:
    """(게이트, 보낸 쪽, 받는 쪽)별 최신 기록의 검사 결과."""
    found: dict[tuple, tuple[str, dict]] = {}
    for f in sorted(gates_dir.glob("G*.json")):
        rec = load_record(f)
        if not rec:
            continue
        key = (rec.get("gate"), rec.get("sender"), rec.get("receiver"))
        order = (str(rec.get("checked_at", "")), f.stat().st_mtime_ns)  # 같은 초에 만든 기록은 수정 시각으로 구분
        if key not in found or order >= found[key][0]:
            found[key] = (order, evaluate(f, base, gates) | {"file": f.name})
    return {k: v[1] for k, v in found.items()}


def cmd_verify(args) -> int:
    """수업 하나의 게이트 기록 연쇄를 확인한다: 필요한 기록 존재·통과·같은 명세 해시."""
    gates = load_gates()
    base = Path(args.root).resolve() if args.root else Path.cwd()
    gdir = base / "tmp" / args.lesson / "gates"
    if not gdir.is_dir():
        print(f"[연쇄 문제] 기록 폴더 없음: {gdir}")
        return EXIT_CHAIN
    recs = latest_records(gdir, base, gates)
    upto = int(args.through[1])
    materials = args.materials.split(",") if args.materials else gates["gates"]["G3"]["receivers"]
    skip = {s.strip() for s in (args.skip or "").split(",") if s.strip()}
    bad = skip - set(gates["gates"])
    if bad:
        print(f"알 수 없는 게이트: {sorted(bad)}", file=sys.stderr)
        return EXIT_INVALID
    for g in sorted(skip):
        print(f"{g}: 생략(통과 아님, 사용자 제공 자료 등 범위 사유를 전달 목록에 기록할 것)")
    problems: list[str] = []

    def need(gate_id, sender, receiver, accept=(0, 10)):
        r = recs.get((gate_id, sender, receiver))
        if r is None:
            problems.append(f"{gate_id} {sender}→{receiver}: 기록 없음(미판정)")
        elif r["code"] not in accept:
            problems.append(f"{gate_id} {sender}→{receiver}: 통과 아님 (종료 코드 {r['code']}, {r['file']})")
        else:
            print(f"{gate_id} {sender}→{receiver}: {r['decision']} ({r['file']})")
        return r if r is not None and r["code"] in accept else None

    chosen: dict[str, dict] = {}
    if upto >= 1 and "G1" not in skip:
        chosen["G1"] = need("G1", "coordinator", "lesson-ideas")
    if upto >= 2 and "G2" not in skip:
        chosen["G2"] = need("G2", "lesson-ideas", "lesson-plan")
    g3 = {}
    if upto >= 3:
        for m in materials:
            r = need("G3", "lesson-plan", m, accept=(0,))
            if r:
                g3[m] = r
        specs = {(r["rec"]["spec"]["version"], r["rec"]["spec"]["sha256"]) for r in g3.values()}
        if len(specs) > 1:
            problems.append(f"G3 기록들의 명세 버전·해시가 서로 다름: {sorted(specs)}")
    frozen = next(iter({(r["rec"]["spec"]["version"], r["rec"]["spec"]["sha256"]) for r in g3.values()}), None)
    g4 = {}
    if upto >= 4:
        for sender in (materials if args.materials else [*materials, "lesson-plan"]):
            r = need("G4", sender, "lesson-review")
            if r:
                g4[sender] = r
                got = (r["rec"]["spec"]["version"], r["rec"]["spec"]["sha256"])
                if frozen and got != frozen:
                    problems.append(f"G4 {sender}: 명세 {got[0]} {got[1][:12]}…가 G3에서 동결한 {frozen[0]} {frozen[1][:12]}…와 다름")
    if upto >= 5:
        r = need("G5", "lesson-review", "coordinator", accept=(0,))
        if r and frozen:
            got = (r["rec"]["spec"]["version"], r["rec"]["spec"]["sha256"])
            if got != frozen:
                problems.append(f"G5: 명세 {got[0]}이(가) G3에서 동결한 {frozen[0]}과 다름")
    if problems:
        print("[연쇄 문제]")
        for p in problems:
            print(" -", p)
        return EXIT_CHAIN
    print(f"[연쇄 정상] {args.through}까지 필요한 기록이 모두 있고 통과했으며 명세 해시가 일치함")
    return 0


def render_tables(gates: dict) -> str:
    parts = []
    for gid, g in gates["gates"].items():
        cond = "조건부 허용" if g.get("allow_conditional") else "조건부 불허"
        parts.append(f"### {gid} {g['name']} (판정: {g['judge_label']}, {cond})")
        parts.append("| ID | 수준 | 점검 질문 |\n|---|---|---|")
        parts += [f"| {c['id']} | {c['level']} | {c['question']} |" for c in g["checks"]]
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def cmd_docs(args) -> int:
    doc = (ROOT / "docs" / "gates.md")
    text = doc.read_text(encoding="utf-8")
    if DOC_START not in text or DOC_END not in text:
        print("docs/gates.md에 생성 구간 표시가 없음", file=sys.stderr)
        return EXIT_INVALID
    head, rest = text.split(DOC_START, 1)
    tail = rest.split(DOC_END, 1)[1]
    new = f"{head}{DOC_START}\n{render_tables(load_gates())}{DOC_END}{tail}"
    if args.write:
        doc.write_text(new, encoding="utf-8")
        return 0
    if args.check:
        if new != text:
            print("docs/gates.md의 항목표가 gates/gates.json과 다름: python tools/gate_check.py docs --write")
            return 1
        return 0
    print(render_tables(load_gates()))
    return 0


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
    n.add_argument("--input", action="append", help="검사 대상 산출물(여러 번 지정). G4·G5는 필수")
    n.add_argument("--previous", help="직전 기록(반려 뒤 재검사 시). 반려 횟수를 자동 계산")
    n.set_defaults(func=cmd_new)

    c = sub.add_parser("check", help="게이트 기록 검사")
    c.add_argument("record")
    c.set_defaults(func=cmd_check)

    v = sub.add_parser("verify", help="수업 하나의 게이트 기록 연쇄 확인")
    v.add_argument("--lesson", required=True)
    v.add_argument("--through", required=True, choices=["G1", "G2", "G3", "G4", "G5"])
    v.add_argument("--materials", help="제작 대상 담당(쉼표). 기본: 활동지·슬라이드·평가 전부. 지정하면 G4도 이 담당만 확인")
    v.add_argument("--skip", help="생략할 게이트(쉼표, 예: G1,G2). 사용자 제공 지도안 등 범위 사유가 있을 때만")
    v.set_defaults(func=cmd_verify)

    d = sub.add_parser("docs", help="docs/gates.md의 항목표를 gates.json에서 생성")
    g = d.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    d.set_defaults(func=cmd_docs)

    h = sub.add_parser("hash", help="SHA-256 출력")
    h.add_argument("files", nargs="+")
    h.set_defaults(func=cmd_hash)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
