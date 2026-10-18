"""자소서 검사기: 글자 수, 상투어(E), 문항 간 숫자 반복."""

from jobhelper import lettercheck

LETTER = """■ 1. 지원동기
최선을 다하겠습니다. 리튬 회수율 10% 개선을 확인했습니다.
■ 2. 직무 역량
회수율 10 % 를 매일 계산했습니다.
"""


def test_counts_without_newlines():
    assert lettercheck.count("가 나\n다") == (4, 3)


def test_flags_cliche_repeated_number_and_limit():
    r = lettercheck.check(LETTER, [20])
    names = [h[1] for s in r["sections"] for h in s["hits"]]
    assert "상투어" in names and "글자 수 초과" in names
    assert r["repeated_numbers"] == {"10%": [0, 1]}
    assert r["errors"] >= 2


def test_clean_letter_passes():
    r = lettercheck.check("■ 1. 지원동기\n정제 공정을 2인 1조로 운전했습니다.", [500])
    assert r["errors"] == 0
    assert "통과" in lettercheck.report(r)


def test_same_ending_needs_same_stem_and_periods_are_not_repeats():
    ok = lettercheck.check("■ 1. 가\n공정을 운전했습니다. 무게를 달았습니다. 결과를 확인했습니다. 1년 일했습니다.\n■ 2. 나\n1년 안에 익히겠습니다.")
    hits = [h[1] for s in ok["sections"] for h in s["hits"]]
    assert "같은 종결 3연속" not in hits and ok["repeated_numbers"] == {}
    bad = lettercheck.check("■ 1. 가\n무게를 확인했습니다. 농도를 확인했습니다. 결과를 확인했습니다.")
    assert "같은 종결 3연속" in [h[1] for h in bad["sections"][0]["hits"]]
