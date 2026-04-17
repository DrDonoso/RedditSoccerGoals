from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from soccergoals.scanner import (
    GOAL_TITLE_PATTERN,
    RedditGoalScanner,
    _clean_scorer,
    _extract_media_url,
    _fuzzy_match_team,
    _make_event_id,
    _normalize_team,
)


# ── Title regex ─────────────────────────────────────────────────────

class TestGoalTitlePattern:
    def test_brackets_with_aggregate(self):
        m = GOAL_TITLE_PATTERN.match(
            "Atletico Madrid [1] - 2 Barcelona [3-2 on agg.] - Ademola Lookman 31'"
        )
        assert m is not None
        assert m.group("home_team") == "Atletico Madrid"
        assert m.group("home_score") == "1"
        assert m.group("away_score") == "2"
        assert m.group("away_team") == "Barcelona"
        assert m.group("scorer") == "Ademola Lookman"
        assert m.group("minute") == "31"

    def test_without_brackets(self):
        m = GOAL_TITLE_PATTERN.match(
            "Atletico Madrid 1 - 0 Barcelona - Ademola Lookman 31'"
        )
        assert m is not None
        assert m.group("home_team") == "Atletico Madrid"
        assert m.group("scorer") == "Ademola Lookman"

    def test_brackets_simple(self):
        m = GOAL_TITLE_PATTERN.match(
            "Real Madrid [1] - 0 Barcelona - Vinícius Júnior 23'"
        )
        assert m is not None
        assert m.group("home_score") == "1"
        assert m.group("away_score") == "0"

    def test_no_match_non_goal_title(self):
        m = GOAL_TITLE_PATTERN.match("Post Match Thread: Real Madrid vs Barcelona")
        assert m is None

    def test_minute_with_plus(self):
        m = GOAL_TITLE_PATTERN.match(
            "Real Madrid [2] - 1 Sevilla - Bellingham 90+"
        )
        assert m is not None
        assert m.group("minute") == "90"


# ── Team normalization & fuzzy match ────────────────────────────────

class TestNormalizeTeam:
    def test_alias_resolution(self):
        assert _normalize_team("Barça") == "barcelona"
        assert _normalize_team("Atleti") == "atletico madrid"
        assert _normalize_team("Spurs") == "tottenham"

    def test_passthrough(self):
        assert _normalize_team("Real Madrid") == "real madrid"


class TestFuzzyMatchTeam:
    def test_exact_match(self):
        assert _fuzzy_match_team("Real Madrid", ["Real Madrid"]) is True

    def test_substring_match(self):
        assert _fuzzy_match_team("Real Madrid CF", ["Real Madrid"]) is True

    def test_alias_match(self):
        assert _fuzzy_match_team("Barça", ["Barcelona"]) is True

    def test_case_insensitive(self):
        assert _fuzzy_match_team("real madrid", ["Real Madrid"]) is True

    def test_no_match(self):
        assert _fuzzy_match_team("Getafe", ["Real Madrid", "Barcelona"]) is False

    def test_no_match_similar_prefix(self):
        """Atletico-MG should NOT match Atletico Madrid."""
        assert _fuzzy_match_team("Atletico-MG", ["Atletico Madrid"]) is False

    def test_empty_candidates(self):
        assert _fuzzy_match_team("Real Madrid", []) is False


# ── Scorer cleaning ────────────────────────────────────────────────

class TestCleanScorer:
    def test_strips_great_goal(self):
        assert _clean_scorer("Igor Matanović great goal") == "Igor Matanović"

    def test_strips_penalty(self):
        assert _clean_scorer("Ricardo Horta penalty") == "Ricardo Horta"

    def test_strips_free_kick(self):
        assert _clean_scorer("Messi free kick") == "Messi"

    def test_strips_own_goal(self):
        assert _clean_scorer("García own goal") == "García"

    def test_strips_header(self):
        assert _clean_scorer("Ramos header") == "Ramos"

    def test_no_qualifier_unchanged(self):
        assert _clean_scorer("Vinícius Júnior") == "Vinícius Júnior"

    def test_strips_golazo(self):
        assert _clean_scorer("Son Heung-min golazo") == "Son Heung-min"

    def test_case_insensitive(self):
        assert _clean_scorer("Bellingham Great Goal") == "Bellingham"


# ── Media URL extraction ────────────────────────────────────────────

class TestExtractMediaUrl:
    def test_streamff_in_url(self):
        url = "https://streamff.link/v/abc123"
        assert _extract_media_url(url, "") == url

    def test_streamff_in_selftext(self):
        result = _extract_media_url(
            "https://reddit.com/post",
            "Check this: https://streamff.link/v/xyz mirror",
        )
        assert result == "https://streamff.link/v/xyz"

    def test_streamable_fallback(self):
        result = _extract_media_url("https://streamable.com/xyz123", "")
        assert result == "https://streamable.com/xyz123"

    def test_no_media_returns_none(self):
        assert _extract_media_url("https://reddit.com/r/soccer/comments/xxx", "") is None

    def test_streamff_preferred_over_streamable(self):
        selftext = (
            "https://streamable.com/old "
            "https://streamff.link/v/new"
        )
        result = _extract_media_url("https://reddit.com/post", selftext)
        assert "streamff.link" in result


# ── Event ID ────────────────────────────────────────────────────────

class TestMakeEventId:
    def test_basic(self):
        dt = datetime(2026, 4, 15, tzinfo=timezone.utc)
        eid = _make_event_id("Real Madrid", "Barcelona", dt)
        assert eid == "real_madrid_vs_barcelona_2026-04-15"

    def test_alias_normalized(self):
        dt = datetime(2026, 4, 15, tzinfo=timezone.utc)
        eid = _make_event_id("Barça", "Atleti", dt)
        assert eid == "barcelona_vs_atletico_madrid_2026-04-15"


# ── RedditGoalScanner integration ──────────────────────────────────


def _make_html_post(
    post_id: str,
    title: str,
    url: str,
    age_seconds: float = 60.0,
) -> str:
    """Build a fake old.reddit.com HTML block for one post."""
    from html import escape
    now_ms = int((datetime.now(timezone.utc).timestamp() - age_seconds) * 1000)
    permalink = f"/r/soccer/comments/{post_id}/slug/"
    return (
        f'<div data-fullname="t3_{post_id}" data-type="link" '
        f'data-timestamp="{now_ms}" '
        f'data-url="{escape(url)}" '
        f'data-permalink="{permalink}" '
        f'data-domain="streamff.link">'
        f'<a class="title may-blank" href="{escape(url)}">{escape(title)}</a>'
        f'</div>'
    )


def _make_html_page(*post_htmls: str, next_url: str | None = None) -> str:
    """Wrap post HTML blocks in a minimal old.reddit.com page."""
    body = "\n".join(post_htmls)
    nav = ""
    if next_url:
        from html import escape
        nav = f'<span class="next-button"><a href="{escape(next_url)}">next</a></span>'
    return f"<html><body>{body}{nav}</body></html>"


def _make_html_response(html: str) -> httpx.Response:
    request = httpx.Request("GET", "https://old.reddit.com/r/soccer/new/")
    return httpx.Response(200, text=html, request=request)


class TestRedditGoalScanner:
    @pytest.fixture()
    def scanner(self, config):
        s = RedditGoalScanner(config)
        yield s

    async def test_finds_goal_brackets(self, scanner):
        post_html = _make_html_post(
            "p1",
            "Real Madrid [1] - 0 Barcelona - Vinícius Júnior 23'",
            "https://streamff.link/v/abc123",
        )
        resp = _make_html_response(_make_html_page(post_html))
        scanner._client = AsyncMock()
        scanner._client.get = AsyncMock(return_value=resp)

        results = await scanner.scan_new_posts(["Real Madrid"])
        assert len(results) == 1
        assert results[0].event.scorer == "Vinícius Júnior"
        assert results[0].post.media_url == "https://streamff.link/v/abc123"

    async def test_finds_goal_no_brackets(self, scanner):
        post_html = _make_html_post(
            "p2",
            "Real Madrid 1 - 0 Barcelona - Vinícius Júnior 23'",
            "https://streamff.link/v/def456",
        )
        resp = _make_html_response(_make_html_page(post_html))
        scanner._client = AsyncMock()
        scanner._client.get = AsyncMock(return_value=resp)

        results = await scanner.scan_new_posts(["Real Madrid"])
        assert len(results) == 1

    async def test_filters_non_monitored_teams(self, scanner):
        post_html = _make_html_post(
            "p3",
            "Getafe [1] - 0 Rayo Vallecano - Borja Mayoral 10'",
            "https://streamff.link/v/xyz",
        )
        resp = _make_html_response(_make_html_page(post_html))
        scanner._client = AsyncMock()
        scanner._client.get = AsyncMock(return_value=resp)

        results = await scanner.scan_new_posts(["Real Madrid", "Barcelona"])
        assert len(results) == 0

    async def test_skips_old_posts(self, scanner):
        post_html = _make_html_post(
            "p4",
            "Real Madrid [1] - 0 Sevilla - Bellingham 55'",
            "https://streamff.link/v/old",
            age_seconds=60 * 20,  # 20 min, config max is 15
        )
        resp = _make_html_response(_make_html_page(post_html))
        scanner._client = AsyncMock()
        scanner._client.get = AsyncMock(return_value=resp)

        results = await scanner.scan_new_posts(["Real Madrid"])
        assert len(results) == 0

    async def test_no_results(self, scanner):
        resp = _make_html_response(_make_html_page())
        scanner._client = AsyncMock()
        scanner._client.get = AsyncMock(return_value=resp)

        results = await scanner.scan_new_posts(["Real Madrid"])
        assert results == []

    async def test_non_goal_post_skipped(self, scanner):
        post_html = _make_html_post(
            "p5",
            "Post Match Thread: Real Madrid vs Barcelona",
            "https://reddit.com/r/soccer/comments/xxx",
        )
        resp = _make_html_response(_make_html_page(post_html))
        scanner._client = AsyncMock()
        scanner._client.get = AsyncMock(return_value=resp)

        results = await scanner.scan_new_posts(["Real Madrid"])
        assert len(results) == 0

    async def test_reddit_exception_returns_empty(self, scanner):
        scanner._client = AsyncMock()
        scanner._client.get = AsyncMock(side_effect=httpx.HTTPError("Reddit down"))

        results = await scanner.scan_new_posts(["Real Madrid"])
        assert results == []

    async def test_alias_match(self, scanner):
        post_html = _make_html_post(
            "p6",
            "Spurs [2] - 1 Arsenal - Son Heung-min 72'",
            "https://streamff.link/v/spurs",
        )
        resp = _make_html_response(_make_html_page(post_html))
        scanner._client = AsyncMock()
        scanner._client.get = AsyncMock(return_value=resp)

        results = await scanner.scan_new_posts(["Tottenham"])
        assert len(results) == 1

    async def test_scorer_qualifier_stripped(self, scanner):
        post_html = _make_html_post(
            "p7",
            "Celta Vigo 0 - [1] Freiburg - Igor Matanović great goal 33'",
            "https://streamff.link/v/celta",
        )
        resp = _make_html_response(_make_html_page(post_html))
        scanner._client = AsyncMock()
        scanner._client.get = AsyncMock(return_value=resp)

        results = await scanner.scan_new_posts(["Celta Vigo"])
        assert len(results) == 1
        assert results[0].event.scorer == "Igor Matanović"
        assert results[0].event.home_scored is False
