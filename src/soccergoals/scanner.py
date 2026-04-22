from __future__ import annotations

import asyncio
import logging
import random
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import unescape

import httpx

from soccergoals.config import Config
from soccergoals.models import GoalEvent, RedditPost, ScanResult

logger = logging.getLogger(__name__)

# old.reddit.com HTML works reliably from Docker (www.reddit.com/...json gets 403)
REDDIT_HTML_URL = "https://old.reddit.com/r/soccer/new/"

# Max pages to fetch when paginating through r/soccer/new
MAX_PAGES = 5
# Retries per request
_MAX_REQUEST_RETRIES = 3
_RETRY_DELAY_SECONDS = 2
# Delay between page fetches to avoid rate-limiting
_PAGE_DELAY_SECONDS = 1.5

GOAL_TITLE_PATTERN = re.compile(
    r"^(?P<home_team>.+?)\s+"           # Home team name (non-greedy)
    r"(?P<home_bracket>\[)?(?P<home_score>\d+)\]?\s*"  # Home score, optional brackets
    r"-\s*"                              # Score separator
    r"(?P<away_bracket>\[)?(?P<away_score>\d+)\]?\s+"  # Away score, optional brackets
    r"(?P<away_team>.+?)\s+"            # Away team name (non-greedy)
    r"(?:\[.*?\]\s*)?"                   # Optional aggregate in brackets (ignored)
    r"-\s+"                              # Separator before scorer
    r"(?P<scorer>.+?)\s+"               # Scorer name (non-greedy)
    r"(?P<minute>\d+)['+]",              # Minute with trailing ' or +
    re.IGNORECASE,
)

STREAMFF_RE = re.compile(r"https?://(?:www\.)?streamff\.(?:link|com)/\S+", re.IGNORECASE)
VIDEO_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:streamable\.com|v\.redd\.it|streamin\.(?:me|link)|streamain\.com|dubz\.link)/\S+",
    re.IGNORECASE,
)

# HTML parsing patterns for old.reddit.com
_POST_DATA_RE = re.compile(
    r'data-fullname="(?P<fullname>t3_[^"]+)".*?'
    r'data-timestamp="(?P<timestamp>\d+)".*?'
    r'data-url="(?P<url>[^"]*)".*?'
    r'data-permalink="(?P<permalink>[^"]*)"',
    re.DOTALL,
)
_TITLE_RE = re.compile(
    r'<a\s+class="[^"]*title[^"]*"[^>]*>(?P<title>[^<]+)</a>',
)
_NEXT_PAGE_RE = re.compile(
    r'<span\s+class="next-button"><a[^>]*href="([^"]*)"',
)

# Qualifier phrases to strip from scorer names (order matters: longest first)
_SCORER_QUALIFIERS_RE = re.compile(
    r"\b(?:great\s+goal|wonderful\s+goal|brilliant\s+goal|amazing\s+goal"
    r"|own\s+goal|free[\s-]kick|header|penalty|volley|long\s+shot|solo\s+goal"
    r"|bicycle\s+kick|chip|lob|brace|hat[\s-]trick|golazo|solo\s+run)\b",
    re.IGNORECASE,
)

# Youth / academy / reserve team indicators — matched at the end of a team name
_YOUTH_TEAM_RE = re.compile(
    r"(?:"
    r"\bU-?(?:1[3-9]|2[0-3])\b"            # U13–U23 (with optional hyphen)
    r"|\bSub-?(?:1[3-9]|2[0-3])\b"          # Sub-13 to Sub-23
    r"|\b(?:Youth|Academy|Juvenil|Primavera|Reserves?)\b"
    r"|\bB\s+team\b"                         # "B team"
    r"|\s+II$"                                # Roman numeral II at end of name
    r")",
    re.IGNORECASE,
)


# Zero-width and directional Unicode characters that Reddit titles may contain
_INVISIBLE_CHARS_RE = re.compile(
    "["
    "\u200b"  # ZERO WIDTH SPACE
    "\u200c"  # ZERO WIDTH NON-JOINER
    "\u200d"  # ZERO WIDTH JOINER
    "\u200e"  # LEFT-TO-RIGHT MARK
    "\u200f"  # RIGHT-TO-LEFT MARK
    "\ufeff"  # BYTE ORDER MARK / ZERO WIDTH NO-BREAK SPACE
    "\u2060"  # WORD JOINER
    "\u2066-\u2069"  # directional isolates
    "\u202a-\u202e"  # directional formatting
    "]+"
)


def _strip_invisible_chars(text: str) -> str:
    """Remove zero-width and directional Unicode characters from text."""
    return _INVISIBLE_CHARS_RE.sub("", text)


def _clean_scorer(name: str) -> str:
    """Remove qualifier phrases (great goal, penalty, etc.) from scorer name."""
    cleaned = _SCORER_QUALIFIERS_RE.sub("", name)
    # Collapse multiple spaces and strip
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _is_youth_team(name: str) -> bool:
    """Return True if *name* looks like a youth / academy / reserve team."""
    return _YOUTH_TEAM_RE.search(name) is not None


TEAM_ALIASES: dict[str, str] = {
    "barça": "barcelona",
    "barca": "barcelona",
    "atleti": "atletico madrid",
    "real": "real madrid",
    "spurs": "tottenham",
    "man utd": "manchester united",
    "man united": "manchester united",
    "man city": "manchester city",
    "bayern": "bayern munich",
    "psg": "paris saint-germain",
    "inter": "inter milan",
    "juve": "juventus",
}


def _strip_accents(text: str) -> str:
    """Remove diacritics/accents from text."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize_team(name: str) -> str:
    """Lowercase, strip accents, resolve aliases."""
    key = _strip_accents(name.strip()).lower()
    return TEAM_ALIASES.get(key, key)


def _fuzzy_match_team(team_name: str, monitored: list[str]) -> bool:
    """Return True if team_name matches any monitored team (case-insensitive, alias-aware, fuzzy)."""
    norm = _normalize_team(team_name)
    for candidate in monitored:
        cand_norm = _normalize_team(candidate)
        if cand_norm in norm or norm in cand_norm:
            return True
        if SequenceMatcher(None, norm, cand_norm).ratio() >= 0.80:
            return True
    return False


def _extract_media_url(url: str, selftext: str) -> str | None:
    """Extract the best video URL from post URL or selftext."""
    for text in (url, selftext):
        match = STREAMFF_RE.search(text)
        if match:
            return match.group(0)
    for text in (url, selftext):
        match = VIDEO_URL_RE.search(text)
        if match:
            return match.group(0)
    return None


def _make_event_id(home_team: str, away_team: str, date: datetime) -> str:
    """Build a deterministic event_id from normalized team names + date."""
    h = _normalize_team(home_team).replace(" ", "_")
    a = _normalize_team(away_team).replace(" ", "_")
    return f"{h}_vs_{a}_{date.strftime('%Y-%m-%d')}"


def _parse_html_posts(html: str) -> list[dict]:
    """Parse old.reddit.com HTML into a list of post dicts."""
    posts: list[dict] = []
    blocks = re.split(r'(?=data-fullname="t3_)', html)
    for block in blocks:
        m = _POST_DATA_RE.search(block)
        if not m:
            continue
        tm = _TITLE_RE.search(block)
        title = unescape(tm.group("title").strip()) if tm else ""
        posts.append({
            "id": m.group("fullname").replace("t3_", ""),
            "fullname": m.group("fullname"),
            "created_utc": int(m.group("timestamp")) / 1000,
            "url": unescape(m.group("url")),
            "permalink": unescape(m.group("permalink")),
            "title": title,
        })
    return posts


class RedditGoalScanner:
    """Browses r/soccer/new via old.reddit.com HTML, parses goal post titles, and returns matching ScanResults."""

    _DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(self, config: Config) -> None:
        self._max_age_minutes = config.max_post_age_minutes
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": self._DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cookie": "over18=1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "Cache-Control": "max-age=0",
            },
            follow_redirects=True,
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _fetch_page(
        self, url: str, page: int,
    ) -> tuple[list[dict] | None, str | None]:
        """Fetch one HTML page from old.reddit.com and parse posts."""
        for attempt in range(1, _MAX_REQUEST_RETRIES + 1):
            try:
                resp = await self._client.get(url)
                if resp.status_code == 429 and attempt < _MAX_REQUEST_RETRIES:
                    delay = _RETRY_DELAY_SECONDS * attempt + random.uniform(0, 2)
                    logger.debug(
                        "Reddit rate-limited (page %d, attempt %d/%d), retrying in %.1fs...",
                        page, attempt, _MAX_REQUEST_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                html = resp.text
                posts = _parse_html_posts(html)

                # Extract next page URL
                next_match = _NEXT_PAGE_RE.search(html)
                next_url = unescape(next_match.group(1)) if next_match else None
                return posts, next_url
            except (httpx.HTTPError, ValueError) as exc:
                if attempt < _MAX_REQUEST_RETRIES:
                    delay = _RETRY_DELAY_SECONDS * attempt + random.uniform(0, 2)
                    logger.debug(
                        "Request error (page %d, attempt %d/%d): %s",
                        page, attempt, _MAX_REQUEST_RETRIES, exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    "Failed to fetch r/soccer/new (page %d) after %d attempts: %s",
                    page, _MAX_REQUEST_RETRIES, exc,
                )
                return None, None
        return None, None

    async def scan_new_posts(
        self, monitored_teams: list[str]
    ) -> list[ScanResult]:
        """Fetch r/soccer/new and return parsed goal events for monitored teams.

        Scrapes old.reddit.com HTML (reliable, no 403 blocks) with pagination
        to cover the full max_post_age window.
        """
        results: list[ScanResult] = []
        now = datetime.now(timezone.utc)
        cutoff_seconds = self._max_age_minutes * 60
        page_url: str | None = f"{REDDIT_HTML_URL}?limit=100"

        for page in range(MAX_PAGES):
            if page > 0:
                await asyncio.sleep(_PAGE_DELAY_SECONDS)
            posts, next_url = await self._fetch_page(page_url, page)
            if posts is None:
                break
            if not posts:
                break

            reached_cutoff = False
            for post_data in posts:
                try:
                    created_utc = post_data.get("created_utc", 0)
                    created = datetime.fromtimestamp(created_utc, tz=timezone.utc)

                    if (now - created).total_seconds() > cutoff_seconds:
                        reached_cutoff = True
                        continue

                    title = post_data.get("title", "")
                    clean_title = _strip_invisible_chars(title)
                    match = GOAL_TITLE_PATTERN.match(clean_title)
                    if not match:
                        continue

                    home_team = match.group("home_team").strip()
                    away_team = match.group("away_team").strip()

                    # Skip youth / academy / reserve team goals
                    if _is_youth_team(home_team) or _is_youth_team(away_team):
                        continue

                    if not (
                        _fuzzy_match_team(home_team, monitored_teams)
                        or _fuzzy_match_team(away_team, monitored_teams)
                    ):
                        continue

                    post_url = post_data.get("url", "")
                    media_url = _extract_media_url(post_url, "")

                    post = RedditPost(
                        post_id=post_data.get("id", ""),
                        title=title,
                        url=post_url,
                        media_url=media_url,
                        score=0,
                        created_utc=created,
                    )

                    # Detect which team scored (bracket position)
                    has_home_bracket = match.group("home_bracket") is not None
                    has_away_bracket = match.group("away_bracket") is not None
                    if has_home_bracket:
                        home_scored = True
                    elif has_away_bracket:
                        home_scored = False
                    else:
                        home_scored = None

                    disallowed = "disallowed" in title.lower()

                    event = GoalEvent(
                        event_id=_make_event_id(home_team, away_team, created),
                        scorer=_clean_scorer(match.group("scorer")),
                        minute=int(match.group("minute")),
                        home_team=home_team,
                        away_team=away_team,
                        home_score=int(match.group("home_score")),
                        away_score=int(match.group("away_score")),
                        timestamp=created,
                        home_scored=home_scored,
                        disallowed=disallowed,
                    )

                    results.append(ScanResult(event=event, post=post))
                except Exception:
                    logger.exception("Error parsing post: %s", post_data.get("id", "?"))
                    continue

            # Stop paginating if we've passed the age cutoff or no more pages
            if reached_cutoff or not next_url:
                break
            page_url = next_url

        logger.info(
            "Scanned r/soccer/new: %d goal posts for monitored teams (%d with media, %d pages)",
            len(results),
            sum(1 for r in results if r.post.media_url),
            page + 1,
        )
        return results
