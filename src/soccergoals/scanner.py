from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher

import httpx

from soccergoals.config import Config
from soccergoals.models import GoalEvent, RedditPost, ScanResult

logger = logging.getLogger(__name__)

REDDIT_NEW_URL = "https://www.reddit.com/r/soccer/new.json"

GOAL_TITLE_PATTERN = re.compile(
    r"^(?P<home_team>.+?)\s+"           # Home team name (non-greedy)
    r"\[?(?P<home_score>\d+)\]?\s*"     # Home score, optional brackets
    r"-\s*"                              # Score separator
    r"\[?(?P<away_score>\d+)\]?\s+"     # Away score, optional brackets
    r"(?P<away_team>.+?)\s+"            # Away team name (non-greedy)
    r"(?:\[.*?\]\s*)?"                   # Optional aggregate in brackets (ignored)
    r"-\s+"                              # Separator before scorer
    r"(?P<scorer>.+?)\s+"               # Scorer name (non-greedy)
    r"(?P<minute>\d+)['+]",              # Minute with trailing ' or +
    re.IGNORECASE,
)

STREAMFF_RE = re.compile(r"https?://(?:www\.)?streamff\.(?:link|com)/\S+", re.IGNORECASE)
VIDEO_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:streamable\.com|v\.redd\.it|streamin\.me|dubz\.link)/\S+",
    re.IGNORECASE,
)

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
        if SequenceMatcher(None, norm, cand_norm).ratio() >= 0.65:
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


class RedditGoalScanner:
    """Browses r/soccer/new via public JSON API, parses goal post titles, and returns matching ScanResults."""

    def __init__(self, config: Config) -> None:
        self._max_age_minutes = config.max_post_age_minutes
        self._user_agent = config.reddit_user_agent
        self._client = httpx.AsyncClient(
            headers={"User-Agent": self._user_agent},
            follow_redirects=True,
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def scan_new_posts(
        self, monitored_teams: list[str]
    ) -> list[ScanResult]:
        """Fetch r/soccer/new and return parsed goal events for monitored teams."""
        results: list[ScanResult] = []
        now = datetime.now(timezone.utc)
        cutoff_seconds = self._max_age_minutes * 60

        try:
            resp = await self._client.get(
                REDDIT_NEW_URL, params={"limit": "50", "raw_json": "1"}
            )
            resp.raise_for_status()
            data = resp.json()
            children = data.get("data", {}).get("children", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Failed to fetch r/soccer/new: %s", exc)
            return []

        for child in children:
            try:
                post_data = child.get("data", {})
                created_utc = post_data.get("created_utc", 0)
                created = datetime.fromtimestamp(created_utc, tz=timezone.utc)

                if (now - created).total_seconds() > cutoff_seconds:
                    continue

                title = post_data.get("title", "")
                match = GOAL_TITLE_PATTERN.match(title)
                if not match:
                    continue

                home_team = match.group("home_team").strip()
                away_team = match.group("away_team").strip()

                if not (
                    _fuzzy_match_team(home_team, monitored_teams)
                    or _fuzzy_match_team(away_team, monitored_teams)
                ):
                    continue

                selftext = post_data.get("selftext", "") or ""
                post_url = post_data.get("url", "")
                media_url = _extract_media_url(post_url, selftext)

                post = RedditPost(
                    post_id=post_data.get("id", ""),
                    title=title,
                    url=post_url,
                    media_url=media_url,
                    score=post_data.get("score", 0),
                    created_utc=created,
                )

                event = GoalEvent(
                    event_id=_make_event_id(home_team, away_team, created),
                    scorer=match.group("scorer").strip(),
                    minute=int(match.group("minute")),
                    home_team=home_team,
                    away_team=away_team,
                    home_score=int(match.group("home_score")),
                    away_score=int(match.group("away_score")),
                    timestamp=created,
                )

                results.append(ScanResult(event=event, post=post))
            except Exception:
                logger.exception("Error parsing post: %s", child.get("data", {}).get("id", "?"))
                continue

        logger.info(
            "Scanned r/soccer/new: %d goal posts for monitored teams (%d with media)",
            len(results),
            sum(1 for r in results if r.post.media_url),
        )
        return results
