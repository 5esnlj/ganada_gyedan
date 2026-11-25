"""Synthetic catalog for local development and the attached frontend."""

from pathlib import Path

from app.core.state import MemoryStore, timestamp
from app.identity.passwords import demo_password_hash
from app.mariadb.files import SEED_FILES
from app.materials.pdf import extract_source_text


def seed_catalog(store: MemoryStore) -> None:
    now = timestamp()
    password_hash = demo_password_hash()
    people = [
        ("t_math", "teacher", "ko", "cls_1", "math"),
        ("t_science", "teacher", "ko", "cls_1", "science"),
        ("t_outside", "teacher", "ko", "cls_2", "math"),
        ("s_a", "student", "zh", "cls_1", None),
        ("s_b", "student", "vi", "cls_1", None),
        ("s_pending", "student", "mn", "cls_1", None),
        ("s_new", "student", "ru", None, None),
        ("s_outside", "student", "ru", "cls_2", None),
        ("t_korean", "teacher", "ko", "cls_1", "korean"),
    ]
    for login, role, language, class_id, subject in people:
        user_id = f"usr_{login}"
        name = {"s_a": "학생 A", "s_b": "학생 B", "t_korean": "국어 교사"}.get(login, login)
        store.users[user_id] = {
            "id": user_id, "loginId": login, "role": role, "displayName": f"{name} (가상)",
            "nativeLanguage": language, "status": "active", "verified": role == "teacher",
            "passwordHash": password_hash,
        }
        if class_id:
            member_id = f"mem_{login}"
            store.memberships[member_id] = {
                "id": member_id, "classId": class_id, "userId": user_id, "role": role,
                "teacherSubject": subject, "status": "active",
            }

    for class_id, name, grade, owner in [
        ("cls_1", "가상 3학년 2반", "중3", "usr_t_math"),
        ("cls_2", "가상 다른 반", "중2", "usr_t_outside"),
        ("cls_reading", "가상 독서반", "중3", "usr_t_korean"),
    ]:
        store.classes[class_id] = {
            "id": class_id, "name": name, "gradeLabel": grade, "academicYear": 2026,
            "ownerId": owner, "status": "active",
        }

    for login in ("t_korean", "s_a"):
        member_id = f"mem_{login}_reading"
        store.memberships[member_id] = {**store.memberships[f"mem_{login}"], "id": member_id, "classId": "cls_reading"}

    sample = Path(__file__).parent / "fixtures" / "sample.pdf"
    for source_id, teacher, title, pdf_path in [
        ("src_math_1", "t_math", "비와 비율 (가상)", sample),
        ("src_science_1", "t_science", "물질의 상태 (가상)", sample),
        ("src_outside_1", "t_outside", "비와 비율 (가상)", sample),
        ("src_korean_1", "t_korean", "글의 중심 내용 찾기 (가상)", SEED_FILES / "seed/src_korean_1/sample.pdf"),
    ]:
        pdf = pdf_path.read_bytes()
        source_text, sentences = extract_source_text(pdf)
        member = store.memberships[f"mem_{teacher}"]
        file_id = f"file_{source_id}"
        location = {"classId": member["classId"], "subject": member["teacherSubject"], "createdBy": member["userId"]}
        store.files[file_id] = {
            **location, "id": file_id, "name": "sample.pdf", "mimeType": "application/pdf",
            "size": len(pdf), "data": pdf, "sourceText": source_text, "sentences": sentences,
            "isSynthetic": True,
        }
        store.sources[source_id] = {
            **location, "id": source_id, "fileId": file_id, "title": title, "unit": "1단원",
            "unitNumber": None, "thumbnailUrl": None, "sourceText": source_text,
            "sentences": sentences, "archived": False, "createdAt": now,
        }

    assignments = [
        ("a_math_1", "src_math_1", "mem_s_a", "published"),
        ("b_math_1", "src_math_1", "mem_s_b", "draft"),
        ("a_science_1", "src_science_1", "mem_s_a", "published"),
        ("outside_1", "src_outside_1", "mem_s_outside", "draft"),
    ]
    for suffix, source_id, student_id, status in assignments:
        source = store.sources[source_id]
        published_at = now if status == "published" else None
        shared = {
            "classId": source["classId"], "subject": source["subject"], "sourceId": source_id,
            "studentMemberId": student_id, "status": status, "version": 1, "createdBy": source["createdBy"],
            "createdAt": now, "publishedAt": published_at, "reviewedAt": published_at,
        }
        if suffix != "outside_1":
            material_id = f"mat_{suffix}"
            store.materials[material_id] = {
                **shared, "id": material_id, "title": source["title"], "revision": 1,
                "content": {"paragraphs": [{"id": "s1", "text": "쉽게 바꾼 가상 학습자료입니다."}], "translations": []},
                "unitNumber": None, "thumbnailUrl": None,
            }
        quiz_id = f"quiz_{suffix}"
        questions = []
        for position in range(1, 4):
            questions.append({
                "id": f"{quiz_id}_q{position}", "position": position, "questionType": "multiple_choice_4",
                "prompt": f"[가상 학습문제] 자료의 설명과 일치하는 것은 무엇인가요? ({position})",
                "choices": [{"id": str(n), "text": f"예시 보기 {n}"} for n in range(1, 5)],
                "correctOptionId": "1", "explanation": "가상 해설입니다. 원본의 첫 문장을 다시 읽어 보세요.",
                "sourceSentenceIds": ["s1"], "maxPoints": 1,
            })
        store.quizzes[quiz_id] = {
            **shared, "id": quiz_id, "title": f"{source['title']} 확인문제", "generationNo": 1,
            "inputMaterialId": None, "questionType": "multiple_choice_4", "questions": questions,
        }
