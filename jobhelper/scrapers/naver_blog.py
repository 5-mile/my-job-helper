"""채용 정보를 올리는 네이버 블로그 RSS 수집기.

블로그마다 제목 규칙이 달라 두 가지 형태를 모두 지원한다.

* ``[알짜중견] 심텍 청주공장 오퍼레이터 채용 ~09/27``
* ``볼보그룹코리아 기계조립 사원 채용 [창원][9월18일까지]``
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..classify import analyze
from ..config import DEFAULT_BLOG_IDS, REQUEST_TIMEOUT, USER_AGENT
from ..dates import format_dday, humanize, parse_deadline, parse_rss_date

log = logging.getLogger(__name__)

RSS_URL = "https://rss.blog.naver.com/{blog_id}.xml"

RECRUIT_HINTS = ("채용", "공고", "모집", "생산", "기술", "현장직", "신입", "경력")

# 회사명이 아니라 글머리 분류로 쓰이는 태그들
_TIER_TAGS = [
    ("대기업", "대기업"),
    ("알짜중견", "중견기업"),
    ("중견기업", "중견기업"),
    ("중견", "중견기업"),
    ("알짜중소", "일반/기타기업"),
    ("중소기업", "일반/기타기업"),
]
_TAG_NOISE = re.compile(r"\[\s*(?:대기업|중견기업|알짜중견|중견|알짜중소|중소기업)\s*\]")
_BRACKET = re.compile(r"[\[\(]([^\]\)]*)[\]\)]")
# "~09/27", "~ 채용시 마감", "[9월18일까지]" 등 마감 표기
_DEADLINE_HINT = re.compile(r"~\s*[^\s\[\]]+(?:\s*마감)?|\[\s*\d{1,2}\s*월\s*\d{1,2}\s*일[^\]]*\]")

# 회사명 뒤에 흔히 붙는 꼬리말 (첫 토큰이 회사명인지 판정할 때 쓴다)
_NON_COMPANY_TOKENS = ("채용", "공고", "모집", "신입", "경력", "하반기", "상반기")


def _pick_parser() -> str:
    """lxml이 있으면 xml 파서를, 없으면 내장 파서를 쓴다."""
    try:
        import lxml  # noqa: F401

        return "xml"
    except ImportError:  # pragma: no cover - 환경 의존
        log.warning("lxml이 없어 html.parser로 RSS를 읽습니다. pip install lxml 권장.")
        return "html.parser"


def detect_tier(raw_title: str) -> str:
    """제목 앞머리의 분류 태그로 기업 규모대를 판정한다."""
    for tag, category in _TIER_TAGS:
        if f"[{tag}]" in raw_title.replace(" ", ""):
            return category
    return "중견기업"


def _clean_title(raw_title: str) -> tuple[str, str]:
    """제목에서 (회사명, 표시용 공고명)을 뽑아낸다."""
    title = _TAG_NOISE.sub("", raw_title).strip()
    title = re.sub(r"\s{2,}", " ", title)

    # 제목이 대괄호로 시작하면 그 안이 회사명인 형태다.
    leading = re.match(r"^[\[\(]([^\]\)]+)[\]\)]", title)
    if leading and leading.group(1).strip():
        company = leading.group(1).strip()
    else:
        tokens = title.split()
        company = tokens[0] if tokens else ""
        # "하반기 ... 채용" 처럼 첫 토큰이 회사명이 아닌 경우를 걸러낸다.
        if company in _NON_COMPANY_TOKENS or company.isdigit():
            company = ""
        company = company[:20]

    if not company:
        company = "추천기업"

    display = _DEADLINE_HINT.sub("", title).strip()
    display = re.sub(r"\s{2,}", " ", display).strip(" -·|")
    return company, display or title


def _parse_item(item) -> dict[str, Any] | None:
    title_node = item.find("title")
    link_node = item.find("link")
    if not (title_node and link_node):
        return None

    raw_title = title_node.get_text(strip=True)
    if not any(hint in raw_title for hint in RECRUIT_HINTS):
        return None

    link = link_node.get_text(strip=True)
    pub_node = item.find("pubDate")
    published = parse_rss_date(pub_node.get_text(strip=True) if pub_node else "")

    company, display_title = _clean_title(raw_title)

    # 마감 표기는 제목 어디에 있든(꼬리표/대괄호) 원문 전체에서 찾는다.
    deadline_text = ""
    hint = _DEADLINE_HINT.search(raw_title)
    if hint:
        deadline_text = hint.group(0)
    deadline = parse_deadline(deadline_text)

    category = detect_tier(raw_title)
    is_large = category == "대기업"
    welfares = analyze(company, raw_title)

    return {
        "source": "블로그",
        "job_key": f"blog:{link}",
        "corp_badge": "🏢 대기업" if is_large else ("🏭 알짜중견" if category == "중견기업" else "🏠 중소/기타"),
        "badge_style": "bg-large-corp" if is_large else "bg-mid-corp",
        "category": category,
        "company": company,
        "position": display_title,
        "published": published.isoformat() if published else "",
        "date": f"📅 {humanize(published)}",
        "deadline": deadline.isoformat() if deadline else "",
        "dday": format_dday(deadline, deadline_text or raw_title),
        "link": link,
        "location": "",
        "welfares": welfares,
    }


def fetch_blog_feed(blog_ids: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """지정한 네이버 블로그들의 최신 채용 글을 최신순으로 모은다."""
    blog_ids = blog_ids or DEFAULT_BLOG_IDS
    parser = _pick_parser()
    feeds: list[dict[str, Any]] = []
    seen_links: set[str] = set()

    for blog_id in blog_ids:
        try:
            res = requests.get(
                RSS_URL.format(blog_id=blog_id),
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            )
            res.raise_for_status()
        except requests.RequestException as exc:
            log.warning("블로그 %s RSS 요청 실패: %s", blog_id, exc)
            continue

        soup = BeautifulSoup(res.content, parser)
        for item in soup.find_all("item"):
            try:
                parsed = _parse_item(item)
            except Exception as exc:
                log.warning("블로그 항목 파싱 실패: %s", exc)
                continue
            if parsed and parsed["link"] not in seen_links:
                seen_links.add(parsed["link"])
                feeds.append(parsed)

    feeds.sort(key=lambda f: f.get("published", ""), reverse=True)
    return feeds[:limit]
