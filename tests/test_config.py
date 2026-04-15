from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestConfigLoading:
    def test_loads_all_required_vars(self, fake_env):
        from soccergoals.config import Config

        cfg = Config()
        assert cfg.reddit_client_id == "test-reddit-id"
        assert cfg.reddit_client_secret == "test-reddit-secret"
        assert cfg.telegram_bot_token == "test-telegram-token"
        assert cfg.telegram_channel_id == "@test_channel"

    def test_missing_required_var_exits(self, fake_env):
        env_without_key = {k: v for k, v in os.environ.items() if k != "REDDIT_CLIENT_ID"}
        with patch.dict(os.environ, env_without_key, clear=True):
            with pytest.raises(SystemExit):
                from soccergoals.config import Config
                Config()

    def test_missing_telegram_token_exits(self, fake_env):
        env_without = {k: v for k, v in os.environ.items() if k != "TELEGRAM_BOT_TOKEN"}
        with patch.dict(os.environ, env_without, clear=True):
            with pytest.raises(SystemExit):
                from soccergoals.config import Config
                Config()

    def test_monitored_teams_comma_parsing(self, fake_env):
        from soccergoals.config import Config

        cfg = Config()
        assert cfg.monitored_teams == ["Real Madrid", "Barcelona", "Manchester City"]

    def test_monitored_teams_strips_whitespace(self, tmp_path):
        env = {
            "REDDIT_CLIENT_ID": "i",
            "REDDIT_CLIENT_SECRET": "s",
            "TELEGRAM_BOT_TOKEN": "t",
            "TELEGRAM_CHANNEL_ID": "@c",
            "MONITORED_TEAMS": "  Liverpool ,  Arsenal  ",
            "DB_PATH": str(tmp_path / "test.db"),
            "TEMP_DIR": str(tmp_path / "tmp"),
        }
        with patch.dict(os.environ, env, clear=False):
            from soccergoals.config import Config
            cfg = Config()
            assert cfg.monitored_teams == ["Liverpool", "Arsenal"]


class TestConfigDefaults:
    def test_default_polling_interval(self, fake_env):
        os.environ.pop("POLLING_INTERVAL_SECONDS", None)
        from soccergoals.config import Config

        cfg = Config()
        assert cfg.polling_interval == 30

    def test_default_max_post_age(self, fake_env):
        os.environ.pop("MAX_POST_AGE_MINUTES", None)
        from soccergoals.config import Config

        cfg = Config()
        assert cfg.max_post_age_minutes == 30

    def test_default_max_retries(self, fake_env):
        os.environ.pop("MAX_RETRIES", None)
        from soccergoals.config import Config

        cfg = Config()
        assert cfg.max_retries == 3

    def test_default_reddit_user_agent(self, fake_env):
        os.environ.pop("REDDIT_USER_AGENT", None)
        from soccergoals.config import Config

        cfg = Config()
        assert cfg.reddit_user_agent == "SoccerGoals/1.0"

    def test_custom_polling_interval(self, fake_env):
        from soccergoals.config import Config

        cfg = Config()
        assert cfg.polling_interval == 10  # set in FAKE_ENV
