"""Connections to the MariaDB instance on the development machine."""

from contextlib import contextmanager
import os

from app.core.errors import fail

REQUIRED_TABLES = frozenset("""
users user_sessions classes class_memberships class_invitations files
diagnostic_forms diagnostic_questions student_diagnostics diagnostic_answers
diagnostic_part_results diagnostic_area_results ai_credentials source_materials
ai_requests student_materials quizzes quiz_questions quiz_submissions quiz_answer_attempts
""".split())


def configured() -> bool:
    return bool(os.getenv("GANADA_DB_HOST"))


def connect():
    import pymysql

    return pymysql.connect(
        host=os.environ["GANADA_DB_HOST"],
        port=int(os.getenv("GANADA_DB_PORT", "3306")),
        database=os.getenv("GANADA_DB_NAME", "ganada"),
        user=os.getenv("GANADA_DB_USER", "ganada_app"),
        password=os.getenv("GANADA_DB_PASSWORD", ""),
        charset="utf8mb4",
        binary_prefix=True,
        connect_timeout=3,
        read_timeout=3,
        write_timeout=3,
        init_command="SET time_zone = '+00:00'",
    )


@contextmanager
def transaction():
    import pymysql

    try:
        with connect() as connection, connection.cursor(pymysql.cursors.DictCursor) as cursor:
            yield cursor
            connection.commit()
    except pymysql.MySQLError:
        fail(503, "DATABASE_UNAVAILABLE", "MariaDB 작업을 완료할 수 없습니다. 서버 연결과 권한을 확인해 주세요.")


def check_connection() -> bool:
    if not configured():
        return False

    with connect() as connection, connection.cursor() as cursor:
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
