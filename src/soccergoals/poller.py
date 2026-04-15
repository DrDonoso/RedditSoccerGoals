from __future__ import annotations

import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher

import httpx

from soccergoals.config import Config
from soccergoals.models import GoalEvent

logger = logging.getLogger(__name__)

API_BASE = "https://v3.football.api-sports.io"


def _fuzzy_match(name: str, candidates: list[str], threshold: float = 0.65) -> bool:
    """Return True if *name* fuzzy-matches any candidate team name."""
    name_lower = name.lower()
    for candidate in candidates:
        candidate_lower = candidate.lower()
        if candidate_lower in name_lower or name_lower in candidate_lower:
            return True
        if SequenceMatcher(None, name_lower, candidate_lower).ratio() >= threshold:
            return True
    return False


class MatchPoller:
    """Polls API-Football for live fixtures and detects new goals."""

    def __init__(self, config: Config) -> None:
        self._api_key = config.football_api_key
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={"x-apisports-key": self._api_key},
            timeout=30.0,
        )
        # match_id -> set of event hashes we already emitted
        self._known_goals: dict[str, set[str]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def poll_live_matches(
        self, monitored_teams: list[str]
    ) -> list[GoalEvent]:
        """Fetch live fixtures and return newly detected GoalEvents."""
        try:
            resp = await self._client.get("/fixtures", params={"live": "all"})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Football API request failed: %s", exc)
            return []

        data = resp.json()
        fixtures = data.get("response", [])
        logger.debug("Polled %d live fixtures", len(fixtures))

        new_goals: list[GoalEvent] = []

        for fixture in fixtures:
            try:
                goals = self._process_fixture(fixture, monitored_teams)
                new_goals.extend(goals)
            except Exception:
                logger.exception(
                    "Error processing fixture %s",
                    fixture.get("fixture", {}).get("id", "?"),
                )

        return new_goals

    def _process_fixture(
        self, fixture: dict, monitored_teams: list[str]
    ) -> list[GoalEvent]:
        """Extract new GoalEvents from a single fixture."""
        info = fixture["fixture"]
        teams = fixture["teams"]
        goals = fixture["goals"]
        events = fixture.get("events", [])

        match_id = str(info["id"])
        home_team = teams["home"]["name"]
        away_team = teams["away"]["name"]

        # Filter: at least one team must be monitored
        if not (
            _fuzzy_match(home_team, monitored_teams)
            or _fuzzy_match(away_team, monitored_teams)
        ):
            return []

        home_score = goals.get("home") or 0
        away_score = goals.get("away") or 0
        known = self._known_goals.setdefault(match_id, set())
        new_goals: list[GoalEvent] = []

        for event in events:
            if event.get("type") != "Goal":
                continue

            minute = event.get("time", {}).get("elapsed") or 0
            player = event.get("player", {}).get("name") or "Unknown"
            assist_name = (event.get("assist") or {}).get("name")
            scoring_team_name = (event.get("team") or {}).get("name") or ""

            event_hash = f"{match_id}:{player}:{minute}"
            if event_hash in known:
                continue

            known.add(event_hash)

            goal = GoalEvent(
                match_id=match_id,
                scorer=player,
                assist=assist_name,
                minute=minute,
                home_team=home_team,
                away_team=away_team,
                home_score=home_score,
                away_score=away_score,
                scoring_team=scoring_team_name,
                aggregate=None,
                timestamp=datetime.now(timezone.utc),
            )
            new_goals.append(goal)
            logger.info(
                "Goal detected: %s %d' — %s %d-%d %s",
                player,
                minute,
                home_team,
                home_score,
                away_score,
                away_team,
            )

        return new_goals
