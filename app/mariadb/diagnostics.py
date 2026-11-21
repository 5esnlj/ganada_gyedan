"""MariaDB forms and diagnostic unit of work."""

from collections import defaultdict
from contextlib import contextmanager
import json

from app.core.errors import fail
from app.database import transaction
from app.diagnostic.dto import AreaResult, PartResult, QuestionDisplay
from app.diagnostic.forms import DiagnosticForm, DiagnosticItem
from app.diagnostic.grading import grade


def load_forms(cursor, store) -> None:
    cursor.execute("SELECT * FROM diagnostic_forms WHERE status IN ('published', 'retired') ORDER BY id")
    rows = cursor.fetchall()
    items = defaultdict(list)
    cursor.execute(
        """SELECT q.*, a.object_key AS audio_key, i.object_key AS image_key
           FROM diagnostic_questions q JOIN diagnostic_forms f ON f.id = q.form_id
           LEFT JOIN files a ON a.id = q.audio_file_id AND a.deleted_at IS NULL AND a.scan_status = 'clean'
           LEFT JOIN files i ON i.id = q.image_file_id AND i.deleted_at IS NULL AND i.scan_status = 'clean'
           WHERE f.status IN ('published', 'retired') ORDER BY q.form_id, q.sequence_no"""
    )
    for row in cursor:
        display = json.loads(row["prompt"])
        display.update(
            id=row["source_code"], sequence=row["sequence_no"], area=row["area"], skill=row["skill"],
            options=json.loads(row["options"]),
            audioUrl="/" + row["audio_key"] if row["audio_key"] else None,
            imageUrl="/" + row["image_key"] if row["image_key"] else None,
        )
        display.setdefault("stimulusText", "")
        items[row["form_id"]].append(DiagnosticItem(
            display=QuestionDisplay.model_validate(display),
            tier=row["tier"], max_points=row["max_points"], gate_slot=row["gate_slot"],
            correct_option_id=row["correct_option_id"],
        ))
    for row in rows:
        rules = json.loads(row["rules_json"])
        form = DiagnosticForm(
            id=row["version"], is_synthetic=rules.get("isSynthetic", False),
            rubric=rules.get("rubric", "dml-v1"), grade_band=rules.get("gradeBand"),
            questions=items[row["id"]],
        )
        store.forms[row["id"]] = form
        if row["status"] == "published":
            if form.grade_band:
                store.assessment_forms[form.grade_band] = form
            elif store.legacy_form is None or not form.is_synthetic:
                store.legacy_form = form


def load_diagnostics(cursor, store, user_id=None) -> None:
    def fetch(sql, student_column="student_id"):
        if user_id is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql + f" WHERE {student_column} = %s", (user_id,))
        return cursor.fetchall()

    records = {}
    for row in fetch("SELECT * FROM student_diagnostics"):
        form = store.forms.get(row["form_id"])
        if form is None:
            raise RuntimeError("진단 기록이 참조하는 문항집을 읽을 수 없습니다.")
        records[str(row["student_id"])] = {
            "form": form, "originClassId": str(row["origin_class_id"]),
            "status": row["status"], "startedAt": row["started_at"],
            "completedAt": row["completed_at"], "answers": {}, "results": None,
        }
    for row in fetch("""SELECT a.student_id, q.source_code, a.selected_option_id
                         FROM diagnostic_answers a JOIN diagnostic_questions q ON q.id = a.question_id""", "a.student_id"):
        records[str(row["student_id"])]["answers"][row["source_code"]] = row["selected_option_id"]

    parts, areas = defaultdict(list), defaultdict(list)
    for row in fetch("SELECT * FROM diagnostic_part_results"):
        parts[str(row["student_id"])].append(PartResult(
            area=row["area"], skill=row["skill"], score=row["score"], level=row["level_code"],
        ))
    for row in fetch("SELECT * FROM diagnostic_area_results"):
        areas[str(row["student_id"])].append(AreaResult(
            area=row["area"], score=row["score"], level=row["level_code"],
        ))
    for key, record in records.items():
        if record["status"] == "completed":
            # The detailed assessment summary is derived from the immutable answer sheet.
            result = grade(record["form"], record["answers"])
            if parts[key]:
                result.parts = parts[key]
            if areas[key]:
                result.areas = areas[key]
            record["results"] = result
    if user_id is None:
        store.diagnostics = records
    else:
        store.diagnostics.pop(user_id, None)
        store.diagnostics.update(records)


@contextmanager
def edit_diagnostic(store, user_id):
    with transaction() as cursor:
        cursor.execute("SELECT id FROM users WHERE id = %s AND role = 'student' AND status = 'active' FOR UPDATE", (user_id,))
        if not cursor.fetchone():
            fail(403, "ROLE_FORBIDDEN", "활성 학생 계정이 필요합니다.")
        load_diagnostics(cursor, store, user_id)
        before = store.diagnostics.get(user_id)
        old_answers = set(before["answers"]) if before else set()
        was_complete = before is not None and before["status"] == "completed"
        yield
        record = store.diagnostics[user_id]
        form_id = next(key for key, value in store.forms.items() if value.id == record["form"].id)
        if before is None:
            cursor.execute(
                """INSERT INTO student_diagnostics (student_id, form_id, origin_class_id)
                   VALUES (%s, %s, %s)""",
                (user_id, form_id, record["originClassId"]),
            )
        for question_id in record["answers"].keys() - old_answers:
            cursor.execute(
                """INSERT INTO diagnostic_answers (student_id, question_id, form_id, selected_option_id)
                   SELECT %s, id, form_id, %s FROM diagnostic_questions WHERE form_id = %s AND source_code = %s""",
                (user_id, record["answers"][question_id], form_id, question_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("진단 문항 ID가 문항집에서 유일하지 않습니다.")
        if record["status"] == "completed" and not was_complete:
            result = record["results"]
            cursor.executemany(
                "INSERT INTO diagnostic_part_results (student_id, area, skill, score, level_code) VALUES (%s,%s,%s,%s,%s)",
                [(user_id, p.area, p.skill, p.score, p.level) for p in result.parts],
            )
            cursor.executemany(
                "INSERT INTO diagnostic_area_results (student_id, area, score, level_code) VALUES (%s,%s,%s,%s)",
                [(user_id, a.area, a.score, a.level) for a in result.areas],
            )
            cursor.execute(
                "UPDATE student_diagnostics SET status = 'completed', completed_at = UTC_TIMESTAMP(6) WHERE student_id = %s",
                (user_id,),
            )
