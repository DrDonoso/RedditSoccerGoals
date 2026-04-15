from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from soccergoals.poller import MatchPoller, _fuzzy_match


# ── Fuzzy matching ──────────────────────────────────────────────────

class TestFuzzyMatch:
    def test_exact_match(self):
        assert _fuzzy_match("Real Madrid", ["Real Madrid"]) is True

    def test_substring_match(self):
        assert _fuzzy_match("Real Madrid CF", ["Real Madrid"]) is True

    def test_reverse_substring(self):
        assert _fuzzy_match("Real Madrid", ["Real Madrid CF"]) is True

    def test_case_insensitive(self):
        assert _fuzzy_match("real madrid", ["Real Madrid"]) is True

    def test_fuzzy_ratio(self):
        # "Manchester City" vs "Man City" should pass with the 0.65 threshold
        assert _fuzzy_match("Manchester City FC", ["Manchester City"]) is True

    def test_no_match(self):
        assert _fuzzy_match("Getafe", ["Real Madrid", "Barcelona"]) is False

    def test_empty_candidates(self):
        assert _fuzzy_match("Real Madrid", []) is False


# ── Fixtures ────────────────────────────────────────────────────────

def _fixture_payload(
    fixture_id: int,
    home: str,
    away: str,
    home_goals: int,
    away_goals: int,
    events: list[dict] | None = None,
) -> dict:
    """Build a single fixture dict matching the API-Football schema."""
    return {
        "fixture": {"id": fixture_id},
        "teams": {"home": {"name": home}, "away": {"name": away}},
        "goals": {"home": home_goals, "away": away_goals},
        "events": events or [],
    }


def _goal_event(player: str, minute: int, team: str) -> dict:
    return {
        "type": "Goal",
        "time": {"elapsed": minute},
        "player": {"name": player},
        "assist": {"name": "Assister"},
        "team": {"name": team},
    }


# ── MatchPoller tests ──────────────────────────────────────────────

class TestMatchPoller:
    @pytest.fixture()
    def poller(self, config):
        p = MatchPoller(config)
        yield p

    async def test_detects_new_goal(self, poller):
        payload = {
            "response": [
                _fixture_payload(
                    1, "Real Madrid", "Sevilla", 1, 0,
                    events=[_goal_event("Vinícius Júnior", 23, "Real Madrid")],
                )
            ]
        }
        mock_resp = httpx.Response(200, json=payload, request=httpx.Request("GET", "/fixtures"))
        with patch.object(poller._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            goals = await poller.poll_live_matches(["Real Madrid"])
        assert len(goals) == 1
        assert goals[0].scorer == "Vinícius Júnior"
        assert goals[0].minute == 23

    async def test_ignores_already_known_goal(self, poller):
        payload = {
            "response": [
                _fixture_payload(
                    1, "Real Madrid", "Sevilla", 1, 0,
                    events=[_goal_event("Vinícius Júnior", 23, "Real Madrid")],
                )
            ]
        }
        mock_resp = httpx.Response(200, json=payload, request=httpx.Request("GET", "/fixtures"))
        with patch.object(poller._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            first = await poller.poll_live_matches(["Real Madrid"])
        with patch.object(poller._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            second = await poller.poll_live_matches(["Real Madrid"])

        assert len(first) == 1
        assert len(second) == 0

    async def test_filters_non_monitored_teams(self, poller):
        payload = {
            "response": [
                _fixture_payload(
                    2, "Getafe", "Rayo Vallecano", 1, 0,
                    events=[_goal_event("Borja Mayoral", 10, "Getafe")],
                )
            ]
        }
        mock_resp = httpx.Response(200, json=payload, request=httpx.Request("GET", "/fixtures"))
        with patch.object(poller._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            goals = await poller.poll_live_matches(["Real Madrid", "Barcelona"])
        assert len(goals) == 0

    async def test_fuzzy_team_name_match(self, poller):
        """'Real Madrid CF' in API data matches monitored 'Real Madrid'."""
        payload = {
            "response": [
                _fixture_payload(
                    3, "Real Madrid CF", "Atlético", 1, 0,
                    events=[_goal_event("Bellingham", 55, "Real Madrid CF")],
                )
            ]
        }
        mock_resp = httpx.Response(200, json=payload, request=httpx.Request("GET", "/fixtures"))
        with patch.object(poller._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            goals = await poller.poll_live_matches(["Real Madrid"])
        assert len(goals) == 1
        assert goals[0].home_team == "Real Madrid CF"

    async def test_api_timeout_returns_empty(self, poller):
        with patch.object(
            poller._client, "get", new_callable=AsyncMock,
            side_effect=httpx.ConnectTimeout("timed out"),
        ):
            goals = await poller.poll_live_matches(["Real Madrid"])
        assert goals == []

    async def test_api_500_returns_empty(self, poller):
        mock_resp = httpx.Response(500, request=httpx.Request("GET", "/fixtures"))
        with patch.object(poller._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            goals = await poller.poll_live_matches(["Real Madrid"])
        assert goals == []

    async def test_empty_response(self, poller):
        payload = {"response": []}
        mock_resp = httpx.Response(200, json=payload, request=httpx.Request("GET", "/fixtures"))
        with patch.object(poller._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            goals = await poller.poll_live_matches(["Real Madrid"])
        assert goals == []

    async def test_multiple_goals_same_fixture(self, poller):
        payload = {
            "response": [
                _fixture_payload(
                    4, "Barcelona", "Valencia", 2, 0,
                    events=[
                        _goal_event("Lewandowski", 30, "Barcelona"),
                        _goal_event("Raphinha", 44, "Barcelona"),
                    ],
                )
            ]
        }
        mock_resp = httpx.Response(200, json=payload, request=httpx.Request("GET", "/fixtures"))
        with patch.object(poller._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            goals = await poller.poll_live_matches(["Barcelona"])
        assert len(goals) == 2
        scorers = {g.scorer for g in goals}
        assert scorers == {"Lewandowski", "Raphinha"}

    async def test_non_goal_events_ignored(self, poller):
        events = [
            {"type": "Card", "time": {"elapsed": 5}, "player": {"name": "X"}, "team": {"name": "Real Madrid"}},
            _goal_event("Vinícius Júnior", 15, "Real Madrid"),
        ]
        payload = {
            "response": [_fixture_payload(5, "Real Madrid", "Betis", 1, 0, events=events)]
        }
        mock_resp = httpx.Response(200, json=payload, request=httpx.Request("GET", "/fixtures"))
        with patch.object(poller._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            goals = await poller.poll_live_matches(["Real Madrid"])
        assert len(goals) == 1
        assert goals[0].scorer == "Vinícius Júnior"
