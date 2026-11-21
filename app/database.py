"""Connection check for the MariaDB instance on the development machine."""

import os

REQUIRED_TABLES = frozenset("""
users user_sessions classes class_memberships class_invitations files
diagnostic_forms diagnostic_questions student_diagnostics diagnostic_answers
diagnostic_part_results diagnostic_area_results ai_credentials source_materials
ai_requests student_materials quizzes quiz_questions quiz_submissions quiz_answer_attempts
""".split())


def check_connection() -> bool:
    host = os.getenv("GANADA_DB_HOST")
    if not host:
        return False

    import pymysql

    with pymysql.connect(
        host=host,
        port=int(os.getenv("GANADA_DB_PORT", "3306")),
        database=os.getenv("GANADA_DB_NAME", "ganada"),
        user=os.getenv("GANADA_DB_USER", "ganada_app"),
        password=os.getenv("GANADA_DB_PASSWORD", ""),
        charset="utf8mb4",
        connect_timeout=3,
        read_timeout=3,
        write_timeout=3,
    ) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT VERSION()")
        if "MariaDB" not in cursor.fetchone()[0]:
            raise RuntimeError("연결된 서버가 MariaDB가 아닙니다.")
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'"
        )
        missing = REQUIRED_TABLES.difference(row[0] for row in cursor)
        if missing:
            raise RuntimeError("MariaDB 테이블 누락: " + ", ".join(sorted(missing)))
    return True


if __name__ == "__main__":
    if not check_connection():
        raise SystemExit("GANADA_DB_HOST 환경변수를 설정해 주세요.")
    print("MariaDB 연결 및 20개 테이블 확인 완료.")
