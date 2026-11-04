"""Provider-independent data adapter. Only the demo provider is functional in Phase 1.
API-Football / Sportmonks adapters are stubs: they never fabricate data and raise
ProviderUnavailable until a licensed key is configured and the mapping is implemented."""
from __future__ import annotations
import os
from abc import ABC, abstractmethod
from typing import List, Dict


class ProviderUnavailable(Exception):
    pass


class FootballDataProvider(ABC):
    name = "abstract"
    @abstractmethod
    def fixtures(self, date: str) -> List[Dict]: ...
    @abstractmethod
    def live_events(self, fixture_id: str) -> List[Dict]: ...
    @abstractmethod
    def lineups(self, fixture_id: str) -> Dict: ...
    def status(self) -> Dict:
        return {"provider": self.name, "available": False}


class DemoProvider(FootballDataProvider):
    name = "demo"
    def fixtures(self, date):
        return [{"id": "demo-1", "home": "Real Madrid (demo)", "away": "Manchester City (demo)", "demo": True}]
    def live_events(self, fixture_id):
        from .demo_data import DEMO_TIMELINE
        return DEMO_TIMELINE
    def lineups(self, fixture_id):
        from .demo_data import northbridge, riverside
        return {"home": [p.name for p in northbridge().starters], "away": [p.name for p in riverside().starters]}
    def status(self):
        return {"provider": self.name, "available": True, "note": "demo data: elite squads, game-estimated ratings"}


class _KeyedStub(FootballDataProvider):
    env = ""
    base_url = ""
    def __init__(self):
        self.key = os.getenv(self.env)
    def _need(self):
        if not self.key:
            raise ProviderUnavailable(f"{self.name}: set {self.env} (licensed key required). Data unavailable - not fabricated.")
        raise ProviderUnavailable(f"{self.name}: key found but adapter mapping not implemented yet (Phase 2).")
    def fixtures(self, date): self._need()
    def live_events(self, fixture_id): self._need()
    def lineups(self, fixture_id): self._need()
    def status(self):
        return {"provider": self.name, "available": False, "key_configured": bool(self.key), "env": self.env,
                "base_url": self.base_url}


class APIFootballProvider(_KeyedStub):
    name, env, base_url = "api-football", "API_FOOTBALL_KEY", "https://v3.football.api-sports.io"


class SportmonksProvider(_KeyedStub):
    name, env, base_url = "sportmonks", "SPORTMONKS_TOKEN", "https://api.sportmonks.com/v3/football"


PROVIDERS = {p.name: p for p in (DemoProvider, APIFootballProvider, SportmonksProvider)}
