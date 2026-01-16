from typing import TYPE_CHECKING, Iterable

import discord

from ballsdex.core.models import BetHistory
from ballsdex.core.utils import menus
from ballsdex.core.utils.paginator import Pages
from ballsdex.packages.betting.betting_user import BettingUser

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


def _get_prefix_emote(bettor: BettingUser) -> str:
    if bettor.cancelled:
        return "\N{NO ENTRY SIGN}"
    elif bettor.accepted:
        return "\N{WHITE HEAVY CHECK MARK}"
    elif bettor.locked:
        return "\N{LOCK}"
    else:
        return ""


def _get_bettor_name(bettor: BettingUser) -> str:
    return f"{_get_prefix_emote(bettor)} {bettor.user.name}"


def _build_list_of_strings(bettor: BettingUser, bot: "BallsDexBot", short: bool = False) -> list[str]:
    proposal: list[str] = [""]
    i = 0

    for nba in bettor.proposal:
        cb_text = nba.description(short=short, include_emoji=True, bot=bot, is_trade=True)
        if bettor.locked:
            text = f"- *{cb_text}*\n"
        else:
            text = f"- {cb_text}\n"
        if bettor.cancelled:
            text = f"~~{text}~~"

        if len(text) + len(proposal[i]) > 950:
            i += 1
            proposal.append("")
        proposal[i] += text

    if not proposal[0]:
        proposal[0] = "*Empty*"

    return proposal


def fill_bet_embed_fields(
    embed: discord.Embed,
    bot: "BallsDexBot",
    bettor1: BettingUser,
    bettor2: BettingUser,
    compact: bool = False,
):
    """Fill the fields of an embed with the NBAs part of a bet."""
    embed.clear_fields()

    compact1 = _build_list_of_strings(bettor1, bot, short=compact)
    compact2 = _build_list_of_strings(bettor2, bot, short=compact)

    embed.add_field(
        name=_get_bettor_name(bettor1),
        value=compact1[0],
        inline=True,
    )
    embed.add_field(
        name=_get_bettor_name(bettor2),
        value=compact2[0],
        inline=True,
    )

    for field_value in compact1[1:] + compact2[1:]:
        embed.add_field(name="\u200b", value=field_value, inline=False)


class BetHistoryFormat(menus.ListPageSource):
    """Pagination source for bet history display"""
    
    def __init__(
        self,
        entries: Iterable[BetHistory],
        header: str,
        bot: "BallsDexBot",
    ):
        self.header = header
        self.bot = bot
        super().__init__(entries, per_page=1)

    async def format_page(self, menu: Pages, bet: BetHistory) -> discord.Embed:
        from ballsdex.core.models import Player
        
        # Load player data by discord_id
        player1 = await Player.get_or_none(discord_id=bet.player1_id)
        player2 = await Player.get_or_none(discord_id=bet.player2_id)
        
        player1_name = player1.username if player1 else f"User #{bet.player1_id}"
        player2_name = player2.username if player2 else f"User #{bet.player2_id}"
        
        embed = discord.Embed(
            title=f"Bet history for {self.header}",
            description=f"Bet ID: {bet.pk:0X}",
            timestamp=bet.bet_date,
        )
        embed.set_footer(
            text=f"Bet {menu.current_page + 1}/{menu.source.get_max_pages()} | Bet date: "
        )
        
        # Determine winner emojis
        p1_emoji = "🎉" if bet.winner_id == bet.player1_id else "❌"
        p2_emoji = "🎉" if bet.winner_id == bet.player2_id else "❌"
        
        # Build descriptions showing stake counts
        p1_desc = f"Staked {bet.player1_count} NBA{'s' if bet.player1_count != 1 else ''}"
        p2_desc = f"Staked {bet.player2_count} NBA{'s' if bet.player2_count != 1 else ''}"
        
        if bet.cancelled:
            p1_desc += "\n*Bet cancelled*"
            p2_desc += "\n*Bet cancelled*"
        
        embed.add_field(
            name=f"{p1_emoji} {player1_name}",
            value=p1_desc,
            inline=True,
        )
        embed.add_field(
            name=f"{p2_emoji} {player2_name}",
            value=p2_desc,
            inline=True,
        )
        
        return embed
