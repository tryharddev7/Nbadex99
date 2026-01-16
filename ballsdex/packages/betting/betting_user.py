from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from ballsdex.core.models import BallInstance, Player


class BettingUser:
    """Represents a player in a bet"""

    def __init__(self, user: discord.User | discord.Member, player: "Player"):
        self.user = user
        self.player = player
        self.proposal: list["BallInstance"] = []
        self.locked = False
        self.accepted = False
        self.cancelled = False
