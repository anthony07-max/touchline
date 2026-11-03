"""Touchline simulation engine (independent of web layer)."""
from .models import Player, Team, Tactics, MatchState
from .simulate import simulate, compare
from .opponent_ai import OpponentAI, PERSONALITIES
