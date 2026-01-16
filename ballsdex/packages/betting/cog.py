import datetime
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Optional

import discord
from cachetools import TTLCache
from discord import app_commands
from discord.ext import commands
from tortoise.expressions import Q

from ballsdex.core.models import BallInstance, Player
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.paginator import Pages
from ballsdex.core.utils.sorting import FilteringChoices, SortingChoices, filter_balls, sort_balls
from ballsdex.core.utils.transformers import (
    BallEnabledTransform,
    BallInstanceTransform,
    SpecialEnabledTransform,
)
from ballsdex.packages.betting.betting_user import BettingUser
from ballsdex.packages.betting.menu import BetMenu

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.betting")

BETTING_GUILD_ID = 1440962506796433519
BETTING_CHANNEL_ID = 1443544409684836382


def betting_channel_check(interaction: discord.Interaction) -> bool:
    """Check if user is in betting channel"""
    return interaction.guild_id == BETTING_GUILD_ID and interaction.channel_id == BETTING_CHANNEL_ID


@app_commands.guild_only()
class Bet(commands.GroupCog):
    """
    Bet NBAs with other players.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.bets: TTLCache[int, dict[int, list[BetMenu]]] = TTLCache(maxsize=999999, ttl=1800)

    def get_bet(
        self,
        interaction: discord.Interaction["BallsDexBot"] | None = None,
        *,
        channel: discord.TextChannel | None = None,
        user: discord.User | discord.Member = None,
    ) -> tuple[BetMenu, BettingUser] | tuple[None, None]:
        """Find an ongoing bet for the given interaction."""
        guild: discord.Guild
        if interaction:
            guild = interaction.guild
            channel = interaction.channel
            user = interaction.user
        elif channel:
            guild = channel.guild
        else:
            raise TypeError("Missing interaction or channel")

        if guild.id not in self.bets:
            self.bets[guild.id] = defaultdict(list)
        if channel.id not in self.bets[guild.id]:
            return (None, None)
        
        to_remove: list[BetMenu] = []
        for bet in self.bets[guild.id][channel.id]:
            if (
                bet.current_view.is_finished()
                or bet.bettor1.cancelled
                or bet.bettor2.cancelled
            ):
                to_remove.append(bet)
                continue
            try:
                bettor = bet._get_bettor(user)
            except RuntimeError:
                continue
            else:
                break
        else:
            for bet in to_remove:
                self.bets[guild.id][channel.id].remove(bet)
            return (None, None)

        for bet in to_remove:
            self.bets[guild.id][channel.id].remove(bet)
        return (bet, bettor)

    @app_commands.command()
    @app_commands.check(betting_channel_check)
    async def begin(self, interaction: discord.Interaction["BallsDexBot"], user: discord.User):
        """
        Begin a bet with the chosen user.

        Parameters
        ----------
        user: discord.User
            The user you want to bet with
        """
        if user.bot:
            await interaction.response.send_message("You cannot bet with bots.", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "You cannot bet with yourself.", ephemeral=True
            )
            return

        player1, _ = await Player.get_or_create(discord_id=interaction.user.id)
        player2, _ = await Player.get_or_create(discord_id=user.id)

        bet1, bettor1 = self.get_bet(interaction)
        bet2, bettor2 = self.get_bet(channel=interaction.channel, user=user)  # type: ignore
        if bet1 or bettor1:
            await interaction.response.send_message(
                "You already have an ongoing bet.", ephemeral=True
            )
            return
        if bet2 or bettor2:
            await interaction.response.send_message(
                "The user you are trying to bet with is already in a bet.", ephemeral=True
            )
            return

        menu = BetMenu(
            self, interaction, BettingUser(interaction.user, player1), BettingUser(user, player2)
        )
        self.bets[interaction.guild.id][interaction.channel.id].append(menu)  # type: ignore
        await menu.start()
        await interaction.response.send_message("Bet started!", ephemeral=True)

    @app_commands.command()
    @app_commands.check(betting_channel_check)
    async def add(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        nba: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ):
        """
        Add an NBA to the ongoing bet.

        Parameters
        ----------
        nba: BallInstance
            The NBA you want to add to your proposal
        special: Special
            Filter the results of autocompletion to a special event. Ignored afterwards.
        """
        if not nba:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        bet, bettor = self.get_bet(interaction)
        if not bet or not bettor:
            await interaction.followup.send("You do not have an ongoing bet.", ephemeral=True)
            return
        
        if bettor.locked:
            await interaction.followup.send(
                "You have locked your proposal, it cannot be edited! "
                "You can click the cancel button to stop the bet instead.",
                ephemeral=True,
            )
            return
        
        if nba in bettor.proposal:
            await interaction.followup.send("You already have this NBA in your proposal.", ephemeral=True)
            return

        if nba.favorite:
            view = ConfirmChoiceView(
                interaction,
                accept_message="NBA added.",
                cancel_message="This request has been cancelled.",
            )
            await interaction.followup.send(
                "This NBA is a favorite, are you sure you want to bet it?",
                view=view,
                ephemeral=True,
            )
            await view.wait()
            if not view.value:
                return

        bettor.proposal.append(nba)
        await interaction.followup.send("NBA added to your proposal.", ephemeral=True)

    @app_commands.command()
    @app_commands.check(betting_channel_check)
    async def remove(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        nba: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ):
        """
        Remove an NBA from the ongoing bet.

        Parameters
        ----------
        nba: BallInstance
            The NBA you want to remove from your proposal
        special: Special
            Filter the results of autocompletion to a special event. Ignored afterwards.
        """
        if not nba:
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        bet, bettor = self.get_bet(interaction)
        if not bet or not bettor:
            await interaction.followup.send("You do not have an ongoing bet.", ephemeral=True)
            return
        
        if bettor.locked:
            await interaction.followup.send(
                "You have locked your proposal, it cannot be edited! "
                "You can click the cancel button to stop the bet instead.",
                ephemeral=True,
            )
            return
        
        if nba in bettor.proposal:
            bettor.proposal.remove(nba)
            await interaction.followup.send("NBA removed from your proposal.", ephemeral=True)
        else:
            await interaction.followup.send("NBA not found in your proposal.", ephemeral=True)

    @app_commands.command()
    @app_commands.check(betting_channel_check)
    async def view(self, interaction: discord.Interaction["BallsDexBot"]):
        """
        View your current bet proposal.
        """
        await interaction.response.defer(ephemeral=True, thinking=True)

        bet, bettor = self.get_bet(interaction)
        if not bet or not bettor:
            await interaction.followup.send("You do not have an ongoing bet.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Your Bet Proposal",
            color=discord.Color.blue(),
        )

        if bettor.proposal:
            lines = [f"- {nba.description(short=True, include_emoji=True, bot=self.bot, is_trade=True)}" for nba in bettor.proposal]
            embed.description = "\n".join(lines)
        else:
            embed.description = "*Empty*"

        embed.set_footer(text=f"Total: {len(bettor.proposal)} NBAs")
        await interaction.followup.send(embed=embed, ephemeral=True)

    bulk = app_commands.Group(name="bulk", description="Bulk betting commands")

    @bulk.command(name="add")
    @app_commands.check(betting_channel_check)
    async def bulk_add(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        nba: BallEnabledTransform | None = None,
        sort: SortingChoices | None = None,
        special: SpecialEnabledTransform | None = None,
        filter: FilteringChoices | None = None,
    ):
        """
        Bulk add NBAs to your bet with filtering options.

        Parameters
        ----------
        nba: Ball
            The NBA you would like to filter the results to
        sort: SortingChoices
            Choose how NBAs are sorted
        special: Special
            Filter the results to a special event
        filter: FilteringChoices
            Filter the results to a specific filter
        """
        await interaction.response.defer(ephemeral=True, thinking=True)

        bet, bettor = self.get_bet(interaction)
        if not bet or not bettor:
            await interaction.followup.send("You do not have an ongoing bet.", ephemeral=True)
            return

        if bettor.locked:
            await interaction.followup.send(
                "You have locked your proposal, it cannot be edited! "
                "You can click the cancel button to stop the bet instead.",
                ephemeral=True,
            )
            return

        from ballsdex.packages.betting.menu import BallsSelector
        
        try:
            query = BallInstance.filter(player__discord_id=interaction.user.id)
            if nba:
                query = query.filter(ball=nba)
            if special:
                query = query.filter(special=special)
            if sort:
                query = sort_balls(sort, query)
            if filter:
                query = filter_balls(filter, query, interaction.guild_id)

            ball_ids = await query.values_list("id", flat=True)
            if not ball_ids:
                await interaction.followup.send(
                    "No NBAs found matching your criteria.", ephemeral=True
                )
                return

            view = BallsSelector(interaction, ball_ids, self)
            await view.start(
                content="Select the NBAs you want to add to your proposal. "
                "Note that the display will wipe on pagination however "
                "the selected NBAs will remain."
            )
        except Exception as e:
            await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)
            log.error(f"Error bulk adding NBAs: {e}", exc_info=True)

async def setup(bot: "BallsDexBot"):
    await bot.add_cog(Bet(bot))
