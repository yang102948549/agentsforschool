"""게이트 검사 회귀 테스트: python -m unittest discover tests"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "gate_check.py"
CONFIRM = {"confirmed": True, "quote": "네, 이 지도안으로 진행해 주세요", "at": "2026-10-14T10:00:00+09:00"}
MATERIALS = ["worksheets", "canva-slides", "formative-assessment"]


def run(*args, cwd):
    p = subprocess.run([sys.executable, str(SCRIPT), "--root", str(cwd), *args],
                       capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout + p.stderr


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "spec.md").write_text("# 명세 v1\nO1 L1-A1\n", encoding="utf-8")
        (self.dir / "out.pdf").write_text("산출물", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, gate="G3", sender="lesson-plan", receiver="worksheets", spec="spec.md",
             version="v1", previous=None, inputs=()):
        args = ["new", gate, "--lesson", "테스트수업", "--sender", sender, "--receiver", receiver,
                "--spec", spec, "--spec-version", version]
        for i in inputs:
            args += ["--input", i]
        if previous:
            args += ["--previous", str(previous)]
        code, out = run(*args, cwd=self.dir)
        self.assertEqual(code, 0, out)
        path = Path(out.strip().splitlines()[-1])
        return path, json.loads(path.read_text(encoding="utf-8"))

    def save(self, path, rec):
        path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")

    def all_pass(self, rec):
        for c in rec["checks"]:
            c["status"] = "pass"
        if "user_confirmation" in rec:
            rec["user_confirmation"] = dict(CONFIRM)
        rec["decision"] = "ready"

    def reject(self, rec, idx=3):
        self.all_pass(rec)
        rec["checks"][idx].update(status="block", reason="시간 합 55분 > 45분", location="지도안 2차시")
        rec["decision"] = "revision_required"
        rec["revision_requests"] = ["전개 활동 10분 축소"]

    def ready(self, gate, sender, receiver, **kw):
        inputs = ["out.pdf"] if gate in ("G4", "G5") else []
        path, rec = self.make(gate, sender, receiver, inputs=inputs, **kw)
        self.all_pass(rec)
        self.save(path, rec)
        return path

    def code(self, path):
        return run("check", str(path), cwd=self.dir)[0]


class RuleTest(Base):
    def test_ready(self):
        path = self.ready("G3", "lesson-plan", "worksheets")
        self.assertEqual(self.code(path), 0)

    def test_untouched_record_is_rejected_without_requests(self):
        path, _ = self.make()
        self.assertEqual(self.code(path), 2)  # revision_requests 없음

    def test_must_block_rejects(self):
        path, rec = self.make()
        self.reject(rec)
        self.save(path, rec)
        self.assertEqual(self.code(path), 20)

    def test_g3_warning_is_revision_required(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["checks"][2].update(status="warning", reason="O3 평가 방향 약함")
        rec["decision"] = "revision_required"
        rec["revision_requests"] = ["O3 평가 방향 보완"]
        self.save(path, rec)
        self.assertEqual(self.code(path), 20)

    def test_g3_warning_cannot_be_conditional(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["checks"][2].update(status="warning", reason="O3 평가 방향 약함")
        rec["decision"] = "conditional_ready"
        rec["conditions"] = ["O3 보완"]
        self.save(path, rec)
        self.assertEqual(self.code(path), 2)  # 규칙 계산(반려)과 불일치

    def test_g4_warning_conditional(self):
        path, rec = self.make("G4", "worksheets", "lesson-review", inputs=["out.pdf"])
        self.all_pass(rec)
        rec["checks"][3].update(status="warning", reason="3쪽 렌더링 기록 누락")
        rec["decision"] = "conditional_ready"
        rec["conditions"] = ["3쪽 렌더링 확인 후 판정"]
        self.save(path, rec)
        self.assertEqual(self.code(path), 10)

    def test_g5_warning_is_revision_required(self):
        path, rec = self.make("G5", "lesson-review", "coordinator", inputs=["out.pdf"])
        self.all_pass(rec)
        rec["checks"][1].update(status="warning", reason="자료 간 문항 번호 1건 불일치")
        rec["decision"] = "revision_required"
        rec["revision_requests"] = ["평가지 문항 3 번호 정정"]
        self.save(path, rec)
        self.assertEqual(self.code(path), 20)

    def test_g1_g2_ready(self):
        self.assertEqual(self.code(self.ready("G1", "coordinator", "lesson-ideas")), 0)
        self.assertEqual(self.code(self.ready("G2", "lesson-ideas", "lesson-plan")), 0)

    def test_should_gap_must_be_listed_in_conditions(self):
        path, rec = self.make()  # G3-07은 should
        self.all_pass(rec)
        rec["checks"][6].update(status="warning", reason="이미지 이용 조건 미확인")
        self.save(path, rec)
        self.assertEqual(self.code(path), 2)  # conditions 없음
        rec["conditions"] = ["이미지 이용 조건 확인 전까지 사용 보류"]
        self.save(path, rec)
        self.assertEqual(self.code(path), 0)  # 조건 불허 게이트의 should는 조건 기재 후 통과

    def test_self_judging_invalid(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["judge"] = "lesson-plan"
        self.save(path, rec)
        self.assertEqual(self.code(path), 2)

    def test_wrong_receiver_invalid(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["receiver"] = rec["judge"] = "lesson-review"
        self.save(path, rec)
        self.assertEqual(self.code(path), 2)

    def test_wrong_sender_invalid(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["sender"] = "worksheets"
        self.save(path, rec)
        self.assertEqual(self.code(path), 2)
        code, _ = run("new", "G3", "--lesson", "x", "--sender", "worksheets", "--receiver", "worksheets",
                      "--spec", "spec.md", "--spec-version", "v1", cwd=self.dir)
        self.assertEqual(code, 2)

    def test_duplicate_check_id_invalid(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["checks"].append(dict(rec["checks"][0]))
        self.save(path, rec)
        self.assertEqual(self.code(path), 2)

    def test_checked_at_must_be_kst(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["checked_at"] = "2026-10-14T01:00:00+00:00"
        self.save(path, rec)
        self.assertEqual(self.code(path), 2)

    def test_g3_requires_user_quote(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["user_confirmation"] = {"confirmed": False, "quote": "", "at": None}
        self.save(path, rec)
        self.assertEqual(self.code(path), 2)

    def test_g4_requires_inputs(self):
        path, rec = self.make("G4", "worksheets", "lesson-review")
        self.all_pass(rec)
        self.save(path, rec)
        self.assertEqual(self.code(path), 2)

    def test_spec_changed_is_stale(self):
        path = self.ready("G3", "lesson-plan", "worksheets")
        (self.dir / "spec.md").write_text("# 명세 v1 수정됨\n", encoding="utf-8")
        self.assertEqual(self.code(path), 3)

    def test_input_changed_is_stale(self):
        path = self.ready("G4", "worksheets", "lesson-review")
        (self.dir / "out.pdf").write_text("바뀐 산출물", encoding="utf-8")
        self.assertEqual(self.code(path), 3)

    def test_new_never_overwrites(self):
        a, _ = self.make()
        b, _ = self.make()
        self.assertNotEqual(a, b)


class RejectionChainTest(Base):
    def test_count_follows_chain_and_escalates_on_third(self):
        r1, rec1 = self.make()
        self.reject(rec1)
        self.save(r1, rec1)
        self.assertEqual(self.code(r1), 20)

        r2, rec2 = self.make(previous=r1)
        self.assertEqual(rec2["rejection_count"], 1)
        self.reject(rec2)
        self.save(r2, rec2)
        self.assertEqual(self.code(r2), 20)

        r3, rec3 = self.make(previous=r2)
        self.assertEqual(rec3["rejection_count"], 2)
        self.reject(rec3)
        self.save(r3, rec3)
        self.assertEqual(self.code(r3), 4)

    def test_cannot_reset_count_by_hand(self):
        r1, rec1 = self.make()
        self.reject(rec1)
        self.save(r1, rec1)
        r2, rec2 = self.make(previous=r1)
        self.reject(rec2)
        rec2["rejection_count"] = 0  # 위조
        self.save(r2, rec2)
        self.assertEqual(self.code(r2), 2)

    def test_count_without_chain_invalid(self):
        path, rec = self.make()
        self.reject(rec)
        rec["rejection_count"] = 2
        self.save(path, rec)
        self.assertEqual(self.code(path), 2)

    def test_new_spec_version_restarts_count(self):
        r1, rec1 = self.make()
        self.reject(rec1)
        self.save(r1, rec1)
        _, rec2 = self.make(previous=r1, version="v2")
        self.assertEqual(rec2["rejection_count"], 0)


class VerifyTest(Base):
    def chain(self, through="G5"):
        self.ready("G1", "coordinator", "lesson-ideas")
        self.ready("G2", "lesson-ideas", "lesson-plan")
        if through in ("G1", "G2"):
            return
        for m in MATERIALS:
            self.ready("G3", "lesson-plan", m)
        for s in [*MATERIALS, "lesson-plan"]:
            self.ready("G4", s, "lesson-review")
        self.ready("G5", "lesson-review", "coordinator")

    def verify(self, through, *extra):
        return run("verify", "--lesson", "테스트수업", "--through", through, *extra, cwd=self.dir)

    def test_full_chain_ok(self):
        self.chain()
        code, out = self.verify("G5")
        self.assertEqual(code, 0, out)

    def test_missing_g3_record(self):
        self.ready("G1", "coordinator", "lesson-ideas")
        self.ready("G2", "lesson-ideas", "lesson-plan")
        self.ready("G3", "lesson-plan", "worksheets")
        code, out = self.verify("G3")
        self.assertEqual(code, 5, out)
        self.assertIn("canva-slides", out)
        # 일부 자료만 요청한 경우에는 해당 담당만 요구한다
        self.assertEqual(self.verify("G3", "--materials", "worksheets")[0], 0)

    def test_g4_spec_hash_differs_from_frozen_g3(self):
        self.chain("G2")
        for m in MATERIALS:
            self.ready("G3", "lesson-plan", m)
        (self.dir / "spec2.md").write_text("# 명세 v1 몰래 수정\n", encoding="utf-8")
        for s in MATERIALS:
            self.ready("G4", s, "lesson-review")
        path, rec = self.make("G4", "lesson-plan", "lesson-review", spec="spec2.md", inputs=["out.pdf"])
        self.all_pass(rec)
        self.save(path, rec)
        code, out = self.verify("G4")
        self.assertEqual(code, 5, out)
        self.assertIn("동결", out)

    def test_rejected_g3_blocks_chain(self):
        self.ready("G1", "coordinator", "lesson-ideas")
        self.ready("G2", "lesson-ideas", "lesson-plan")
        for m in MATERIALS[:2]:
            self.ready("G3", "lesson-plan", m)
        path, rec = self.make("G3", "lesson-plan", MATERIALS[2])
        self.reject(rec)
        self.save(path, rec)
        self.assertEqual(self.verify("G3")[0], 5)


class DocsTest(unittest.TestCase):
    def test_docs_tables_match_gates_json(self):
        p = subprocess.run([sys.executable, str(SCRIPT), "docs", "--check"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
