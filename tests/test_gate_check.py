"""게이트 검사 회귀 테스트: python -m unittest discover tests"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "gate_check.py"


def run(*args, cwd):
    p = subprocess.run([sys.executable, str(SCRIPT), "--root", str(cwd), *args],
                       capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout + p.stderr


class GateCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "spec.md").write_text("# 명세 v1\nO1 L1-A1\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, gate="G3", sender="lesson-plan", receiver="worksheets"):
        code, out = run("new", gate, "--lesson", "테스트수업", "--sender", sender,
                        "--receiver", receiver, "--spec", "spec.md", "--spec-version", "v1", cwd=self.dir)
        self.assertEqual(code, 0, out)
        path = Path(out.strip().splitlines()[-1])
        return path, json.loads(path.read_text(encoding="utf-8"))

    def save(self, path, rec):
        path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")

    def all_pass(self, rec):
        for c in rec["checks"]:
            c["status"] = "pass"
        if "user_confirmation" in rec:
            rec["user_confirmation"] = {"confirmed": True, "quote": "네, 이 지도안으로 진행해 주세요", "at": "2026-10-14T10:00:00+09:00"}
        rec["decision"] = "ready"

    def test_ready(self):
        path, rec = self.make()
        self.all_pass(rec)
        self.save(path, rec)
        self.assertEqual(run("check", str(path), cwd=self.dir)[0], 0)

    def test_untouched_record_is_rejected_without_requests(self):
        path, _ = self.make()
        self.assertEqual(run("check", str(path), cwd=self.dir)[0], 2)  # revision_requests 없음

    def test_must_block_rejects(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["checks"][3].update(status="block", reason="시간 합 55분 > 45분", location="지도안 2차시")
        rec["decision"] = "revision_required"
        rec["revision_requests"] = ["2차시 전개 활동 10분 축소"]
        self.save(path, rec)
        self.assertEqual(run("check", str(path), cwd=self.dir)[0], 20)

    def test_g3_warning_not_conditional(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["checks"][2].update(status="warning", reason="O3 평가 방향 약함")
        rec["decision"] = "conditional_ready"
        rec["conditions"] = ["O3 보완"]
        self.save(path, rec)
        code, out = run("check", str(path), cwd=self.dir)
        self.assertEqual(code, 2, out)  # G3는 조건부 불허 → 계산 결과와 불일치

    def test_g4_warning_conditional(self):
        path, rec = self.make("G4", "worksheets", "lesson-review")
        self.all_pass(rec)
        rec["checks"][3].update(status="warning", reason="3쪽 렌더링 기록 누락")
        rec["decision"] = "conditional_ready"
        rec["conditions"] = ["3쪽 렌더링 확인 후 판정"]
        self.save(path, rec)
        self.assertEqual(run("check", str(path), cwd=self.dir)[0], 10)

    def test_self_judging_invalid(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["judge"] = "lesson-plan"
        self.save(path, rec)
        self.assertEqual(run("check", str(path), cwd=self.dir)[0], 2)

    def test_wrong_receiver_invalid(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["receiver"] = rec["judge"] = "lesson-review"
        self.save(path, rec)
        self.assertEqual(run("check", str(path), cwd=self.dir)[0], 2)

    def test_g3_requires_user_quote(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["user_confirmation"] = {"confirmed": False, "quote": "", "at": None}
        self.save(path, rec)
        self.assertEqual(run("check", str(path), cwd=self.dir)[0], 2)

    def test_spec_changed_is_stale(self):
        path, rec = self.make()
        self.all_pass(rec)
        self.save(path, rec)
        (self.dir / "spec.md").write_text("# 명세 v1 수정됨\n", encoding="utf-8")
        self.assertEqual(run("check", str(path), cwd=self.dir)[0], 3)

    def test_rejection_limit_escalates(self):
        path, rec = self.make()
        self.all_pass(rec)
        rec["checks"][0].update(status="block", reason="사용자 확인 없음")
        rec["user_confirmation"]["confirmed"] = False
        rec["decision"] = "revision_required"
        rec["revision_requests"] = ["사용자 확인 요청"]
        rec["rejection_count"] = 2
        self.save(path, rec)
        self.assertEqual(run("check", str(path), cwd=self.dir)[0], 4)

    def test_missing_reason_invalid(self):
        path, rec = self.make("G1", "coordinator", "lesson-ideas")
        self.all_pass(rec)
        rec["checks"][3]["status"] = "warning"
        rec["decision"] = "conditional_ready"
        rec["conditions"] = ["교재 상태 확인"]
        self.save(path, rec)
        self.assertEqual(run("check", str(path), cwd=self.dir)[0], 2)


if __name__ == "__main__":
    unittest.main()
