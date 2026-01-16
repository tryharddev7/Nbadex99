import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, AsyncIterator, List, Set, cast

import discord
from discord.ui import Button, View, button
from discord.utils import format_dt, utcnow

from ballsdex.core.models import BallInstance
from ballsdex.core.utils import menus
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.paginator import Pages
from ballsdex.packages.betting.betting_user import BettingUser
from ballsdex.packages.betting.display import fill_bet_embed_fields
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot
    from ballsdex.packages.betting.cog import Bet as BetCog

log = logging.getLogger("ballsdex.packages.betting.menu")
BET_TIMEOUT = 30


class InvalidBetOperation(Exception):
    pass


class BetView(View):
    """Interactive view for adding and managing bets"""
    
    def __init__(self, bet: "BetMenu"):
        super().__init__(timeout=60 * BET_TIMEOUT + 1)
        self.bet = bet

    async def interaction_check(self, interaction: discord.Interaction["BallsDexBot"], /) -> bool:
        try:
            self.bet._get_bettor(interaction.user)
        except RuntimeError:
            await interaction.response.send_message(
                "You are not allowed to interact with this bet.", ephemeral=True
            )
            return False
        else:
            return True

    @button(label="Lock proposal", emoji="\N{LOCK}", style=discord.ButtonStyle.primary)
    async def lock(self, interaction: discord.Interaction["BallsDexBot"], button: Button):
        bettor = self.bet._get_bettor(interaction.user)
        if bettor.locked:
            await interaction.response.send_message(
                "You have already locked your proposal!", ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        await self.bet.lock(bettor)
        if self.bet.bettor1.locked and self.bet.bettor2.locked:
            await interaction.followup.send(
                "Your proposal has been locked. Now confirm again to end the bet.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "Your proposal has been locked. "
                "You can wait for the other user to lock their proposal.",
                ephemeral=True,
            )

    @button(label="Reset", emoji="\N{DASH SYMBOL}", style=discord.ButtonStyle.secondary)
    async def clear(self, interaction: discord.Interaction["BallsDexBot"], button: Button):
        bettor = self.bet._get_bettor(interaction.user)
        await interaction.response.defer(thinking=True, ephemeral=True)

        if bettor.locked:
            await interaction.followup.send(
                "You have locked your proposal, it cannot be edited! "
                "You can click the cancel button to stop the bet instead.",
                ephemeral=True,
            )
            return

        view = ConfirmChoiceView(
            interaction,
            accept_message="Clearing your proposal...",
            cancel_message="This request has been cancelled.",
        )
        await interaction.followup.send(
            "Are you sure you want to clear your proposal?", view=view, ephemeral=True
        )
        await view.wait()
        if not view.value:
            return

        if bettor.locked:
            await interaction.followup.send(
                "You have locked your proposal, it cannot be edited! "
                "You can click the cancel button to stop the bet instead.",
                ephemeral=True,
            )
            return

        bettor.proposal.clear()
        await interaction.followup.send("Proposal cleared.", ephemeral=True)

    @button(
        label="Cancel bet",
        emoji="\N{HEAVY MULTIPLICATION X}\N{VARIATION SELECTOR-16}",
        style=discord.ButtonStyle.danger,
    )
    async def cancel(self, interaction: discord.Interaction["BallsDexBot"], button: Button):
        await interaction.response.defer(thinking=True, ephemeral=True)

        view = ConfirmChoiceView(
            interaction,
            accept_message="Cancelling the bet...",
            cancel_message="This request has been cancelled.",
        )
        await interaction.followup.send(
            "Are you sure you want to cancel this bet?", view=view, ephemeral=True
        )
        await view.wait()
        if not view.value:
            return

        await self.bet.user_cancel(self.bet._get_bettor(interaction.user))
        await interaction.followup.send("Bet has been cancelled.", ephemeral=True)


class ConfirmView(View):
    def __init__(self, bet: "BetMenu"):
        super().__init__(timeout=60 * 14 + 55)
        self.bet = bet

    async def on_timeout(self):
        if self.bet.task:
            self.bet.task.cancel()
        await self.bet.cancel("The bet has timed out.")

    async def interaction_check(self, interaction: discord.Interaction["BallsDexBot"], /) -> bool:
        try:
            self.bet._get_bettor(interaction.user)
        except RuntimeError:
            await interaction.response.send_message(
                "You are not allowed to interact with this bet.", ephemeral=True
            )
            return False
        else:
            return True

    @discord.ui.button(
        style=discord.ButtonStyle.success, emoji="\N{HEAVY CHECK MARK}\N{VARIATION SELECTOR-16}"
    )
    async def accept_button(self, interaction: discord.Interaction["BallsDexBot"], button: Button):
        bettor = self.bet._get_bettor(interaction.user)
        await interaction.response.defer(ephemeral=True, thinking=True)
        if bettor.accepted:
            await interaction.response.send_message(
                "You have already accepted this bet.", ephemeral=True
            )
            return
        result = await self.bet.confirm(bettor)
        if self.bet.bettor1.accepted and self.bet.bettor2.accepted:
            if result:
                await interaction.followup.send("The bet is now concluded.", ephemeral=True)
            else:
                await interaction.followup.send(
                    ":warning: An error occurred while concluding the bet.", ephemeral=True
                )
        else:
            await interaction.followup.send(
                "You have accepted the bet, waiting for the other user...", ephemeral=True
            )

    @discord.ui.button(
        style=discord.ButtonStyle.danger,
        emoji="\N{HEAVY MULTIPLICATION X}\N{VARIATION SELECTOR-16}",
    )
    async def deny_button(self, interaction: discord.Interaction["BallsDexBot"], button: Button):
        await interaction.response.defer(thinking=True, ephemeral=True)

        view = ConfirmChoiceView(
            interaction,
            accept_message="Cancelling the bet...",
            cancel_message="This request has been cancelled.",
        )
        await interaction.followup.send(
            "Are you sure you want to cancel this bet?", view=view, ephemeral=True
        )
        await view.wait()
        if not view.value:
            return

        if self.bet.bettor1.accepted and self.bet.bettor2.accepted:
            await interaction.followup.send(
                "You can't cancel now; the bet has already gone through."
            )
            return

        await self.bet.user_cancel(self.bet._get_bettor(interaction.user))
        await interaction.followup.send("Bet has been cancelled.", ephemeral=True)


class BetMenu:
    def __init__(
        self,
        cog: "BetCog",
        interaction: discord.Interaction["BallsDexBot"],
        bettor1: BettingUser,
        bettor2: BettingUser,
    ):
        self.cog = cog
        self.bot = interaction.client
        self.channel: discord.TextChannel = cast(discord.TextChannel, interaction.channel)
        self.bettor1 = bettor1
        self.bettor2 = bettor2
        self.embed = discord.Embed()
        self.task: asyncio.Task | None = None
        self.current_view: BetView | ConfirmView = BetView(self)
        self.message: discord.Message
        self.cooldown_start_time: datetime | None = None

    def _get_bettor(self, user: discord.User | discord.Member) -> BettingUser:
        if user.id == self.bettor1.user.id:
            return self.bettor1
        elif user.id == self.bettor2.user.id:
            return self.bettor2
        raise RuntimeError(f"User with ID {user.id} cannot be found in the bet")

    def _generate_embed(self):
        add_command = "`/bet add`"
        remove_command = "`/bet remove`"
        view_command = "`/bet view`"

        self.embed.title = "NBA betting"
        self.embed.color = discord.Colour.blurple()
        self.embed.description = (
            f"Add or remove NBAs you want to propose to the other player using the {add_command} "
            f"and {remove_command} commands.\n"
            "Once you're finished, click the lock button below to confirm your proposal.\n"
            "You can also lock with nothing if you're receiving a gift.\n\n"
            "*This bet will timeout "
            f"{format_dt(utcnow() + timedelta(minutes=BET_TIMEOUT), style='R')}.*\n\n"
            f"Use the {view_command} command to see the full list of NBAs."
        )
        self.embed.set_footer(
            text="This message is updated every 15 seconds, "
            "but you can keep on editing your proposal."
        )

    async def update_message_loop(self):
        """A loop task that updates every 15 seconds with the new content."""
        assert self.task
        start_time = utcnow()

        while True:
            await asyncio.sleep(15)
            if utcnow() - start_time > timedelta(minutes=BET_TIMEOUT):
                self.bot.loop.create_task(self.cancel("The bet timed out"))
                return

            try:
                fill_bet_embed_fields(self.embed, self.bot, self.bettor1, self.bettor2)
                await self.message.edit(embed=self.embed)
            except Exception:
                log.exception(
                    f"Failed to refresh the bet menu "
                    f"bettor1={self.bettor1.user.id} bettor2={self.bettor2.user.id}"
                )
                self.bot.loop.create_task(self.cancel("The bet errored"))
                return

    async def start(self):
        """Start the bet by sending the initial message and opening up the proposals."""
        self._generate_embed()
        fill_bet_embed_fields(self.embed, self.bot, self.bettor1, self.bettor2)
        self.message = await self.channel.send(
            content=f"Hey {self.bettor2.user.mention}, {self.bettor1.user.name} "
            "is proposing a bet with you!",
            embed=self.embed,
            view=self.current_view,
        )
        self.task = self.bot.loop.create_task(self.update_message_loop())

    async def cancel(self, reason: str = "The bet has been cancelled."):
        """Cancel the bet immediately."""
        if self.task:
            self.task.cancel()
        self.current_view.stop()

        for item in self.current_view.children:
            item.disabled = True  # type: ignore

        fill_bet_embed_fields(self.embed, self.bot, self.bettor1, self.bettor2)
        self.embed.colour = discord.Colour.dark_red()
        self.embed.description = f"**{reason}**"
        if getattr(self, "message", None):
            await self.message.edit(content=None, embed=self.embed, view=self.current_view)

    async def lock(self, bettor: BettingUser):
        """Mark a user's proposal as locked, ready for next stage"""
        bettor.locked = True
        if self.bettor1.locked and self.bettor2.locked:
            if self.task:
                self.task.cancel()
            if not self.bettor1.proposal and not self.bettor2.proposal:
                await self.cancel("Nothing has been proposed in the bet, it has been cancelled.")
                return
            self.current_view.stop()
            fill_bet_embed_fields(self.embed, self.bot, self.bettor1, self.bettor2)

            self.embed.colour = discord.Colour.yellow()
            self.embed.description = (
                "Both users locked their proposals! Now confirm to conclude this bet."
            )
            self.cooldown_start_time = datetime.now(timezone.utc)
            self.current_view = ConfirmView(self)
            await self.message.edit(content=None, embed=self.embed, view=self.current_view)

    async def user_cancel(self, bettor: BettingUser):
        """Register a user request to cancel the bet"""
        bettor.cancelled = True
        await self.cancel()

    async def confirm(self, bettor: BettingUser) -> bool:
        """Mark a user's proposal as accepted. If both users accept, end the bet now"""
        result = True
        bettor.accepted = True
        fill_bet_embed_fields(self.embed, self.bot, self.bettor1, self.bettor2)
        if self.bettor1.accepted and self.bettor2.accepted:
            if self.task and not self.task.cancelled():
                self.task.cancel()

            # Randomly select winner and perform bet resolution
            winner_is_bettor1 = random.choice([True, False])
            winner = self.bettor1 if winner_is_bettor1 else self.bettor2
            loser = self.bettor2 if winner_is_bettor1 else self.bettor1

            # Transfer loser's NBAs to winner
            try:
                for nba in loser.proposal:
                    nba.player = winner.player
                    await nba.save()
            except Exception as e:
                log.error(f"Error transferring NBAs: {e}")
                self.embed.description = "Error concluding bet!"
                self.embed.colour = discord.Colour.red()
                result = False
            else:
                self.embed.description = f"🎉 {winner.user.name} won the bet!"
                self.embed.colour = discord.Colour.green()

            self.current_view.stop()
            for item in self.current_view.children:
                item.disabled = True  # type: ignore

        await self.message.edit(content=None, embed=self.embed, view=self.current_view)
        return result


class BallsSource(menus.ListPageSource):
    """Pagination source for ball selection"""
    
    def __init__(self, entries: list[int]):
        super().__init__(entries, per_page=25)
        self.cache: dict[int, BallInstance] = {}

    async def prepare(self):
        first_entries = (
            self.entries[: self.per_page * 5]
            if len(self.entries) > self.per_page * 5
            else self.entries
        )
        balls = await BallInstance.filter(id__in=first_entries)
        for ball in balls:
            self.cache[ball.pk] = ball

    async def fetch_page(self, ball_ids: list[int]) -> AsyncIterator[BallInstance]:
        if ball_ids[0] not in self.cache:
            async for ball in BallInstance.filter(id__in=ball_ids):
                self.cache[ball.pk] = ball
        for id in ball_ids:
            yield self.cache[id]

    async def format_page(self, menu: "BallsSelector", ball_ids: list[int]):
        await menu.set_options(self.fetch_page(ball_ids))
        return True


class BallsSelector(Pages):
    """Selector for bulk adding NBAs to bet"""
    
    def __init__(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        balls: list[int],
        cog: "BetCog",
    ):
        self.bot = interaction.client
        self.interaction = interaction
        source = BallsSource(balls)
        super().__init__(source, interaction=interaction)
        self.add_item(self.select_ball_menu)
        self.add_item(self.confirm_button)
        self.add_item(self.select_all_button)
        self.add_item(self.clear_button)
        self.balls_selected: Set[BallInstance] = set()
        self.cog = cog

    async def set_options(self, balls: AsyncIterator[BallInstance]):
        options: List[discord.SelectOption] = []
        async for ball in balls:
            emoji = self.bot.get_emoji(int(ball.countryball.emoji_id))
            favorite = f"{settings.favorited_collectible_emoji} " if ball.favorite else ""
            special = ball.special_emoji(self.bot, True)
            options.append(
                discord.SelectOption(
                    label=f"{favorite}{special}#{ball.pk:0X} {ball.countryball.country}",
                    description=f"ATK: {ball.attack_bonus:+d}% • HP: {ball.health_bonus:+d}% • "
                    f"Caught on {ball.catch_date.strftime('%d/%m/%y %H:%M')}",
                    emoji=emoji,
                    value=f"{ball.pk}",
                    default=ball in self.balls_selected,
                )
            )
        self.select_ball_menu.options = options
        self.select_ball_menu.max_values = len(options)

    @discord.ui.select(min_values=1, max_values=25)
    async def select_ball_menu(
        self, interaction: discord.Interaction["BallsDexBot"], item: discord.ui.Select
    ):
        for value in item.values:
            ball_instance = await BallInstance.get(id=int(value)).prefetch_related(
                "ball", "player"
            )
            self.balls_selected.add(ball_instance)
        await interaction.response.defer()

    @discord.ui.button(label="Select Page", style=discord.ButtonStyle.secondary)
    async def select_all_button(
        self, interaction: discord.Interaction["BallsDexBot"], button: Button
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)
        for ball in self.select_ball_menu.options:
            ball_instance = await BallInstance.get(id=int(ball.value)).prefetch_related(
                "ball", "player"
            )
            if ball_instance not in self.balls_selected:
                self.balls_selected.add(ball_instance)
        await interaction.followup.send(
            "All NBAs on this page have been selected.\n"
            "Note that the menu may not reflect this change until you change page.",
            ephemeral=True,
        )

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.primary)
    async def confirm_button(
        self, interaction: discord.Interaction["BallsDexBot"], button: Button
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)
        bet, bettor = self.cog.get_bet(interaction)
        if bet is None or bettor is None:
            return await interaction.followup.send(
                "The bet has been cancelled or the user is not part of the bet.",
                ephemeral=True,
            )
        if bettor.locked:
            return await interaction.followup.send(
                "You have locked your proposal, it cannot be edited! "
                "You can click the cancel button to stop the bet instead.",
                ephemeral=True,
            )
        if any(ball in bettor.proposal for ball in self.balls_selected):
            return await interaction.followup.send(
                "You have already added some of the NBAs you selected.",
                ephemeral=True,
            )

        if len(self.balls_selected) == 0:
            return await interaction.followup.send(
                "You have not selected any NBAs to add to your proposal.",
                ephemeral=True,
            )
        
        for ball in self.balls_selected:
            if ball.favorite:
                view = ConfirmChoiceView(interaction)
                await interaction.followup.send(
                    "One or more of the NBAs is favorited, "
                    "are you sure you want to add it to the bet?",
                    view=view,
                    ephemeral=True,
                )
                await view.wait()
                if not view.value:
                    return
                break
        
        for ball in self.balls_selected:
            bettor.proposal.append(ball)
        
        grammar = "NBA" if len(self.balls_selected) == 1 else "NBAs"
        await interaction.followup.send(
            f"{len(self.balls_selected)} {grammar} added to your proposal.", ephemeral=True
        )
        self.balls_selected.clear()

    @discord.ui.button(label="Clear", style=discord.ButtonStyle.danger)
    async def clear_button(self, interaction: discord.Interaction["BallsDexBot"], button: Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        self.balls_selected.clear()
        await interaction.followup.send(
            "You have cleared all currently selected NBAs.\n"
            "This does not affect NBAs within your bet proposal.\n"
            "There may be an instance where it shows NBAs on the"
            " current page as selected, this is not the case - "
            "changing page will show the correct state.",
            ephemeral=True,
        )
