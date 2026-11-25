"""Learning criteria from 영역_등급별_고려요소.pdf, shared by PDF conversion and quiz generation."""

from typing import Literal, TypedDict

from app.core.dto import Area, Level
from app.diagnostic.dto import DiagnosticResults

LEVEL_LABELS = {
    "below_beginner": "초급 미달", "beginner": "초급", "intermediate": "중급", "advanced": "고급",
}

CommunicationStatus = Literal["needs_improvement", "adequate"]
TextTreatment = Literal["simplify", "explain", "preserve"]


class SubjectCriterion(TypedDict):
    grade: str
    textTreatment: TextTreatment
    learnerState: str
    guidance: str


class CommunicationCriterion(TypedDict):
    status: CommunicationStatus
    label: str
    learnerState: str
    guidance: str


SUBJECT_CRITERIA: dict[Area, dict[Level, tuple[str, str]]] = {
    "korean": {
        "beginner": (
            "국어 교과에서 쓰는 어휘 지식이 부족하다.",
            "반복되는 핵심어(인물, 갈등, 비유 등)의 뜻을 반드시 풀이한다. 단원의 중심 주제를 쉬운 말로 풀어 쓴다.",
        ),
        "intermediate": (
            "기본 어휘는 알지만 개념어와 긴 지문의 흐름을 따라가기 어렵다.",
            "핵심 개념어는 원문을 유지하고 뜻풀이를 붙인다. 길고 추상적인 표현에는 중심 내용을 이해할 수 있는 설명을 덧붙인다.",
        ),
        "advanced": (
            "국어 교과의 어휘와 지문을 대부분 이해한다.",
            "원문을 유지하고 낯선 문학 용어나 한자어 같은 전문 표현에만 설명을 붙인다.",
        ),
    },
    "math": {
        "beginner": (
            "문장과 수학 용어를 읽고 무엇을 구해야 하는지 파악하기 어렵다.",
            "수학 용어(둘레, 약수 등)의 뜻을 반드시 풀이한다. 조건과 지시어(구하시오, 각각, 모두)는 쉽게 풀어 쓴다. 숫자와 식은 그대로 둔다.",
        ),
        "intermediate": (
            "기본 수학 용어는 알지만 조건이 여러 개인 문장제에서 관계를 놓치기 쉽다.",
            "핵심 용어에 뜻풀이를 붙인다. 조건이 겹치는 긴 문장은 원문을 유지하고 조건 사이의 관계를 설명한다.",
        ),
        "advanced": (
            "수학 문장과 용어를 대부분 이해한다.",
            "원문을 유지하고 단원에서 새로 나오는 개념어에만 설명을 붙인다.",
        ),
    },
    "social": {
        "beginner": (
            "사회 교과의 개념어와 역사·제도·문화 배경지식이 부족하다.",
            "개념어(재판, 권리 등)와 문화·역사 배경어(백성, 공양미 등)의 뜻을 반드시 풀이한다. 단원의 중심 주제와 사건의 흐름을 쉽게 풀어 쓴다.",
        ),
        "intermediate": (
            "개념어를 들어 보았지만 개념 사이의 관계와 배경을 연결하기 어렵다.",
            "개념어는 원문을 유지하고 뜻풀이를 붙인다. 배경지식이 필요한 표현에는 짧은 설명을 덧붙인다.",
        ),
        "advanced": (
            "사회 교과의 개념과 글을 대부분 이해한다.",
            "원문을 유지하고 낯선 제도·역사 용어에만 설명을 붙인다.",
        ),
    },
    "science": {
        "beginner": (
            "과학 용어와 실험·관찰을 설명하는 문장을 이해하기 어렵다.",
            "과학 용어(관찰, 변화, 원리 등)의 뜻을 반드시 풀이한다. 실험 과정과 결과, 원인과 결과의 흐름을 쉽게 풀어 쓴다.",
        ),
        "intermediate": (
            "기본 과학 용어는 알지만 과정이나 원리를 설명하는 긴 문장을 따라가기 어렵다.",
            "핵심 용어에 뜻풀이를 붙인다. 과정·원리를 설명하는 문장은 원문을 유지하고 짧은 설명을 덧붙인다.",
        ),
        "advanced": (
            "과학 용어와 설명을 대부분 이해한다.",
            "원문을 유지하고 새로 나오는 전문 용어에만 설명을 붙인다.",
        ),
    },
}

COMMUNICATION_CRITERIA: dict[CommunicationStatus, CommunicationCriterion] = {
    "needs_improvement": {
        "status": "needs_improvement", "label": "보완 필요",
        "learnerState": "일상 한국어 표현과 교실 지시문도 이해하기 어렵다.",
        "guidance": "일상 표현까지 쉬운 말로 바꾸고 문장을 짧게 나눈다. 활동 지시문(써 보세요, 이야기해 보세요 등)도 쉽게 풀어 쓴다.",
    },
    "adequate": {
        "status": "adequate", "label": "양호",
        "learnerState": "일상 한국어로 소통하는 데 큰 어려움이 없다.",
        "guidance": "일상 표현을 유지하고 해당 교과 등급에 맞춰 교과 어휘와 내용에 집중한다.",
    },
}

TEXT_TREATMENT: dict[Level, TextTreatment] = {
    "beginner": "simplify", "intermediate": "explain", "advanced": "preserve",
}


def area_level(results: DiagnosticResults, area: Area) -> Level | None:
    return next((item.level for item in results.areas if item.area == area), None)


def reading_level(results: DiagnosticResults, area: Area) -> Level | None:
    return next((item.level for item in results.parts if item.area == area and item.skill == "reading"), None)


def communication_status(results: DiagnosticResults) -> CommunicationStatus | None:
    if results.assessmentSummary is not None:
        return results.assessmentSummary.communication.status
    level = area_level(results, "daily")
    if level is None:
        return None
    return "needs_improvement" if level in {"below_beginner", "beginner"} else "adequate"


def subject_criterion(subject: Area, level: Level | None) -> SubjectCriterion | None:
    if subject == "daily" or level is None:
        return None
    # The supplied rubric has no separate below-beginner column.
    grade = "beginner" if level == "below_beginner" else level
    state, guidance = SUBJECT_CRITERIA[subject][grade]
    if level == "below_beginner":
        guidance += " 초급 기준을 적용하되 더 짧은 문장과 익숙한 낱말을 사용한다."
    return {
        "grade": LEVEL_LABELS[level], "textTreatment": TEXT_TREATMENT[grade],
        "learnerState": state, "guidance": guidance,
    }


def communication_criterion(status: CommunicationStatus | None) -> CommunicationCriterion | None:
    return COMMUNICATION_CRITERIA[status] if status is not None else None
