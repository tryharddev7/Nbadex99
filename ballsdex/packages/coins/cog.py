import logging
import random
from typing import TYPE_CHECKING, Optional, List, Set, AsyncIterator, cast

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button
from tortoise import timezone
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from ballsdex.core.models import (
    Ball,
    BallInstance,
    Pack,
    PackOpenHistory,
    Player,
    PlayerPack,
    Special,
)
from ballsdex.core.utils import menus
from ballsdex.core.utils.paginator import Pages
from ballsdex.core.utils.sorting import FilteringChoices, SortingChoices, filter_balls, sort_balls
from ballsdex.core.utils.transformers import BallInstanceTransform, BallEnabledTransform, SpecialEnabledTransform
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.coins")

_active_operations: Set[int] = set()


class ConfirmView(discord.ui.View):
    def __init__(self, user: discord.User | discord.Member, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.user = user
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This is not your confirmation!", ephemeral=True)
            return False
        return True

    @discord.ui.button(emoji="✔", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(emoji="✖", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()


class BulkSellSource(menus.ListPageSource):
    def __init__(self, entries: List[int]):
        super().__init__(entries, per_page=25)
        self.cache: dict[int, BallInstance] = {}

    async def prepare(self):
        first_entries = (
            self.entries[: self.per_page * 5]
            if len(self.entries) > self.per_page * 5
            else self.entries
        )
        balls = await BallInstance.filter(id__in=first_entries).prefetch_related("ball", "special")
        for ball in balls:
            self.cache[ball.pk] = ball

    async def fetch_page(self, ball_ids: List[int]) -> AsyncIterator[BallInstance]:
        if ball_ids and ball_ids[0] not in self.cache:
            async for ball in BallInstance.filter(id__in=ball_ids).prefetch_related("ball", "special"):
                self.cache[ball.pk] = ball
        for id in ball_ids:
            if id in self.cache:
                yield self.cache[id]

    async def format_page(self, menu: "BulkSellSelector", ball_ids: List[int]):
        await menu.set_options(self.fetch_page(ball_ids))
        return True


class BulkSellSelector(Pages):
    def __init__(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        balls: List[int],
    ):
        self.bot = interaction.client
        self.interaction = interaction
        source = BulkSellSource(balls)
        super().__init__(source, interaction=interaction)
        self.source = source
        self.add_item(self.select_ball_menu)
        self.add_item(self.confirm_button)
        self.add_item(self.select_all_button)
        self.add_item(self.clear_button)
        self.balls_selected: Set[int] = set()
        self.confirmed = False

    async def set_options(self, balls: AsyncIterator[BallInstance]):
        options: List[discord.SelectOption] = []
        async for ball in balls:
            if ball.favorite or ball.deleted:
                continue
            emoji = self.bot.get_emoji(int(ball.countryball.emoji_id))
            special = ball.special_emoji(self.bot, True)
            value = ball.countryball.quicksell_value
            if ball.specialcard:
                value = int(value * 1.5)
            options.append(
                discord.SelectOption(
                    label=f"{special}#{ball.pk:0X} {ball.countryball.country}",
                    description=f"ATK: {ball.attack_bonus:+d}% • HP: {ball.health_bonus:+d}% • {value:,} coins",
                    emoji=emoji,
                    value=f"{ball.pk}",
                    default=ball.pk in self.balls_selected,
                )
            )
        if options:
            self.select_ball_menu.options = options
            self.select_ball_menu.max_values = len(options)
            self.select_ball_menu.min_values = 0
            self.select_ball_menu.disabled = False
        else:
            self.select_ball_menu.options = [
                discord.SelectOption(label="No NBAs available", value="none")
            ]
            self.select_ball_menu.max_values = 1
            self.select_ball_menu.min_values = 1
            self.select_ball_menu.disabled = True

    @discord.ui.select(min_values=1, max_values=25)
    async def select_ball_menu(
        self, interaction: discord.Interaction["BallsDexBot"], item: discord.ui.Select
    ):
        await interaction.response.defer()
        for value in item.values:
            if value == "none":
                continue
            ball_id = int(value)
            if ball_id in self.source.cache:
                self.balls_selected.add(ball_id)
            else:
                ball_instance = await BallInstance.get(id=ball_id).prefetch_related("ball", "special")
                self.source.cache[ball_id] = ball_instance
                self.balls_selected.add(ball_id)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.primary)
    async def confirm_button(
        self, interaction: discord.Interaction["BallsDexBot"], button: Button
    ):
        await interaction.response.defer()
        if len(self.balls_selected) == 0:
            await interaction.followup.send(
                f"You have not selected any {settings.plural_collectible_name} to sell.",
                ephemeral=True,
            )
            return
        self.confirmed = True
        self.stop()

    @discord.ui.button(label="Select Page", style=discord.ButtonStyle.secondary)
    async def select_all_button(
        self, interaction: discord.Interaction["BallsDexBot"], button: Button
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)
        for opt in self.select_ball_menu.options:
            if opt.value == "none":
                continue
            ball_id = int(opt.value)
            self.balls_selected.add(ball_id)
        await interaction.followup.send(
            f"All {settings.plural_collectible_name} on this page have been selected.\n"
            "Note that the menu may not reflect this change until you change page.",
            ephemeral=True,
        )

    @discord.ui.button(label="Clear", style=discord.ButtonStyle.danger)
    async def clear_button(
        self, interaction: discord.Interaction["BallsDexBot"], button: Button
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)
        self.balls_selected.clear()
        await interaction.followup.send(
            f"You have cleared all currently selected {settings.plural_collectible_name}.\n"
            "There may be an instance where it shows selected items on the current page, "
            "this is not the case - changing page will show the correct state.",
            ephemeral=True,
        )


class PackTransform(app_commands.Transformer):
    async def transform(self, interaction: discord.Interaction, value: str) -> Pack:
        try:
            pack = await Pack.get(id=int(value))
        except Exception:
            pack = await Pack.filter(name__icontains=value).first()
        if not pack:
            raise app_commands.TransformerError(value, type(value), self)
        return pack

    async def autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            packs = await Pack.filter(enabled=True).order_by("price")
            choices = []
            for pack in packs:
                if current.lower() in pack.name.lower():
                    emoji = pack.emoji + " " if pack.emoji else ""
                    choices.append(app_commands.Choice(
                        name=f"{emoji}{pack.name} - {pack.price:,} coins",
                        value=str(pack.id)
                    ))
            return choices[:25]
        except Exception:
            return []


class OwnedPackTransform(app_commands.Transformer):
    async def transform(self, interaction: discord.Interaction, value: str) -> PlayerPack:
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        try:
            player_pack = await PlayerPack.get(id=int(value), player=player)
        except Exception:
            player_pack = await PlayerPack.filter(
                player=player, pack__name__icontains=value, quantity__gt=0
            ).first()
        if not player_pack or player_pack.quantity <= 0:
            raise app_commands.TransformerError(value, type(value), self)
        return player_pack

    async def autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            player, _ = await Player.get_or_create(discord_id=interaction.user.id)
            player_packs = await PlayerPack.filter(player=player, quantity__gt=0).prefetch_related("pack")
            choices = []
            for pp in player_packs:
                if current.lower() in pp.pack.name.lower():
                    emoji = pp.pack.emoji + " " if pp.pack.emoji else ""
                    choices.append(app_commands.Choice(
                        name=f"{emoji}{pp.pack.name} x{pp.quantity}",
                        value=str(pp.id)
                    ))
            return choices[:25]
        except Exception:
            return []


class Coins(commands.GroupCog, group_name="coins"):
    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    async def balance(self, interaction: discord.Interaction):
        """
        Check your coins balance.
        """
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        
        embed = discord.Embed(
            title="Coins Balance",
            description=f"{interaction.user.mention} has **{player.coins:,}** coins",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command()
    async def leaderboard(self, interaction: discord.Interaction):
        """
        View the top 10 users with the most coins.
        """
        await interaction.response.defer()
        
        top_players = await Player.filter(coins__gt=0).order_by("-coins").limit(10)
        
        if not top_players:
            await interaction.followup.send("No players with coins found!")
            return
        
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        
        for i, player in enumerate(top_players):
            try:
                user = await self.bot.fetch_user(player.discord_id)
                username = user.display_name
            except Exception:
                username = f"Unknown User"
            
            if i < 3:
                lines.append(f"{medals[i]} **{username}** — `{player.coins:,}` coins")
            else:
                lines.append(f"`#{i+1}` {username} — `{player.coins:,}` coins")
        
        embed = discord.Embed(
            title="💰 Richest Players",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command()
    async def give(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        amount: int,
    ):
        """
        Give coins to another user.

        Parameters
        ----------
        user: discord.User
            The user you want to give coins to
        amount: int
            Number of coins to give
        """
        if user.id == interaction.user.id:
            await interaction.response.send_message("You cannot give coins to yourself!", ephemeral=True)
            return
        
        if user.bot:
            await interaction.response.send_message("You cannot give coins to bots!", ephemeral=True)
            return
        
        if amount < 1:
            await interaction.response.send_message("Amount must be at least 1!", ephemeral=True)
            return
        
        if interaction.user.id in _active_operations:
            await interaction.response.send_message("You have another operation in progress!", ephemeral=True)
            return
        
        _active_operations.add(interaction.user.id)
        try:
            async with in_transaction():
                player = await Player.get_or_none(discord_id=interaction.user.id)
                if not player:
                    player = await Player.create(discord_id=interaction.user.id)
                
                if player.coins < amount:
                    await interaction.response.send_message(
                        f"You don't have enough coins! You have **{player.coins:,}** coins.",
                        ephemeral=True
                    )
                    return
                
                recipient, _ = await Player.get_or_create(discord_id=user.id)
                
                player.coins -= amount
                recipient.coins += amount
                await player.save(update_fields=["coins"])
                await recipient.save(update_fields=["coins"])
            
            await interaction.response.send_message(
                f"{interaction.user.mention} gave **{amount:,}** coins to {user.mention}!\n"
                f"New balance: **{player.coins:,}** coins"
            )
        finally:
            _active_operations.discard(interaction.user.id)

    @app_commands.command()
    async def sell(
        self,
        interaction: discord.Interaction,
        countryball: BallInstanceTransform,
    ):
        """
        Sell an NBA for coins (quicksell).

        Parameters
        ----------
        countryball: BallInstance
            The NBA you want to sell
        """
        if countryball.favorite:
            await interaction.response.send_message(
                f"You cannot sell a favorited {settings.collectible_name}!",
                ephemeral=True
            )
            return

        if not countryball.is_tradeable:
            await interaction.response.send_message(
                f"This {settings.collectible_name} cannot be sold!",
                ephemeral=True
            )
            return

        if interaction.user.id in _active_operations:
            await interaction.response.send_message("You have another operation in progress!", ephemeral=True)
            return

        _active_operations.add(interaction.user.id)
        try:
            await countryball.lock_for_trade()
            
            ball = countryball.countryball
            sell_value = ball.quicksell_value
            
            bonus_multiplier = 1.0
            if countryball.specialcard:
                bonus_multiplier = 1.5
            
            final_value = int(sell_value * bonus_multiplier)
            
            attack = "{:+}".format(countryball.attack_bonus)
            health = "{:+}".format(countryball.health_bonus)
            special_text = f" ({countryball.specialcard.name})" if countryball.specialcard else ""
            
            embed = discord.Embed(
                title="Confirm Quicksell",
                description=(
                    f"Are you sure you want to sell **#{countryball.pk:0X} {ball.country}{special_text}** "
                    f"({attack}%/{health}%) for **{final_value:,}** coins?"
                ),
                color=discord.Color.orange()
            )
            
            view = ConfirmView(interaction.user)
            await interaction.response.send_message(embed=embed, view=view)
            
            await view.wait()
            
            if view.value is None:
                await countryball.unlock()
                embed.description = "Quicksell timed out."
                embed.color = discord.Color.greyple()
                await interaction.edit_original_response(embed=embed, view=None)
                return
            
            if not view.value:
                await countryball.unlock()
                embed.description = "Quicksell cancelled."
                embed.color = discord.Color.red()
                await interaction.edit_original_response(embed=embed, view=None)
                return
            
            async with in_transaction():
                player = await Player.get(discord_id=interaction.user.id)
                await countryball.refresh_from_db()
                
                if countryball.player_id != player.pk or countryball.deleted:
                    await countryball.unlock()
                    embed.description = f"You no longer own this {settings.collectible_name}!"
                    embed.color = discord.Color.red()
                    await interaction.edit_original_response(embed=embed, view=None)
                    return
                
                countryball.deleted = True
                await countryball.save(update_fields=["deleted"])
                await countryball.unlock()
                
                player.coins += final_value
                await player.save(update_fields=["coins"])
            
            embed.title = "Quicksell Complete!"
            embed.description = (
                f"You sold **#{countryball.pk:0X} {ball.country}{special_text}** for **{final_value:,}** coins!\n"
                f"New balance: **{player.coins:,}** coins"
            )
            embed.color = discord.Color.green()
            await interaction.edit_original_response(embed=embed, view=None)
        except Exception:
            try:
                await countryball.unlock()
            except Exception:
                pass
            raise
        finally:
            _active_operations.discard(interaction.user.id)

    @app_commands.command()
    async def bulk_sell(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        countryball: BallEnabledTransform | None = None,
        sort: SortingChoices | None = None,
        special: SpecialEnabledTransform | None = None,
        filter: FilteringChoices | None = None,
    ):
        """
        Bulk sell nbas for coins, with paramaters to aid with searching.

        Parameters
        ----------
        countryball: Ball
            The nba you would like to filter the results to
        sort: SortingChoices
            Choose how nbas are sorted. Can be used to show duplicates.
        special: Special
            Filter the results to a special event
        filter: FilteringChoices
            Filter the results to a specific filter
        """
        if interaction.user.id in _active_operations:
            await interaction.response.send_message("You have another operation in progress!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        
        query = BallInstance.filter(player=player, favorite=False, tradeable=True, deleted=False)
        
        if countryball:
            query = query.filter(ball=countryball)
        if special:
            query = query.filter(special=special)
        if sort:
            query = sort_balls(sort, query)
        if filter:
            query = filter_balls(filter, query, interaction.guild_id)
        
        balls = cast(list[int], await query.values_list("id", flat=True))
        
        if not balls:
            await interaction.followup.send(
                f"No {settings.plural_collectible_name} found.", ephemeral=True
            )
            return
        
        view = BulkSellSelector(interaction, balls)
        await view.start(
            content=f"Select the {settings.plural_collectible_name} you want to sell, "
            "note that the display will wipe on pagination however "
            f"the selected {settings.plural_collectible_name} will remain."
        )
        
        await view.wait()
        
        if not view.confirmed or not view.balls_selected:
            return
        
        if interaction.user.id in _active_operations:
            await interaction.edit_original_response(
                content="You have another operation in progress!", embed=None, view=None
            )
            return
        
        _active_operations.add(interaction.user.id)
        locked_balls = []
        try:
            valid_balls = await BallInstance.filter(
                id__in=list(view.balls_selected),
                player=player,
                deleted=False,
                favorite=False,
                locked__isnull=True
            ).prefetch_related("ball", "special")
            
            for inst in valid_balls:
                await inst.lock_for_trade()
                locked_balls.append(inst)
            
            if not locked_balls:
                await interaction.edit_original_response(
                    content=None,
                    embed=discord.Embed(
                        title="Bulk Sell Failed",
                        description="None of the selected NBAs could be sold. They may have been traded or locked.",
                        color=discord.Color.red()
                    ),
                    view=None
                )
                return
            
            total_value = 0
            for inst in locked_balls:
                value = inst.countryball.quicksell_value
                if inst.specialcard:
                    value = int(value * 1.5)
                total_value += value
            
            confirm_embed = discord.Embed(
                title="Confirm Bulk Sell",
                description=(
                    f"Are you sure you want to sell **{len(locked_balls)}** "
                    f"{settings.plural_collectible_name} for **{total_value:,}** coins?\n\n"
                    f"This action cannot be undone!"
                ),
                color=discord.Color.orange()
            )
            
            confirm_view = ConfirmView(interaction.user)
            await interaction.edit_original_response(content=None, embed=confirm_embed, view=confirm_view)
            
            await confirm_view.wait()
            
            if confirm_view.value is None or not confirm_view.value:
                for inst in locked_balls:
                    await inst.unlock()
                confirm_embed.title = "Bulk Sell Cancelled"
                confirm_embed.description = "You cancelled the bulk sell."
                confirm_embed.color = discord.Color.red()
                await interaction.edit_original_response(embed=confirm_embed, view=None)
                return
            
            sold_count = 0
            actual_value = 0
            
            async with in_transaction():
                await player.refresh_from_db()
                
                for inst in locked_balls:
                    await inst.refresh_from_db()
                    if inst.player_id == player.pk and not inst.deleted:
                        value = inst.countryball.quicksell_value
                        if inst.specialcard:
                            value = int(value * 1.5)
                        actual_value += value
                        inst.deleted = True
                        await inst.save(update_fields=["deleted"])
                        sold_count += 1
                    await inst.unlock()
                
                player.coins += actual_value
                await player.save(update_fields=["coins"])
            
            skipped = len(locked_balls) - sold_count
            skip_text = f"\n({skipped} skipped)" if skipped > 0 else ""
            embed = discord.Embed(
                title="Bulk Quicksell Complete!",
                description=(
                    f"You sold **{sold_count}** {settings.plural_collectible_name} for **{actual_value:,}** coins!{skip_text}\n"
                    f"New balance: **{player.coins:,}** coins"
                ),
                color=discord.Color.green()
            )
            await interaction.edit_original_response(embed=embed, view=None)
        except Exception:
            for inst in locked_balls:
                try:
                    await inst.unlock()
                except Exception:
                    pass
            raise
        finally:
            _active_operations.discard(interaction.user.id)


class Packs(commands.GroupCog, group_name="pack"):
    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    async def list(self, interaction: discord.Interaction):
        """
        View all available packs you can buy.
        """
        packs = await Pack.filter(enabled=True).order_by("price").prefetch_related("special")
        
        if not packs:
            await interaction.response.send_message(
                "No packs are currently available!",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="Available Packs",
            description=f"Here are the packs you can buy with coins:",
            color=discord.Color.blue()
        )
        
        for pack in packs:
            emoji = pack.emoji + " " if pack.emoji else ""
            description = pack.description if pack.description else "No description"
            limit_text = f"\nDaily Limit: {pack.daily_limit}" if pack.daily_limit > 0 else ""
            
            embed.add_field(
                name=f"{emoji}{pack.name}",
                value=(
                    f"Price: **{pack.price:,}** coins\n"
                    f"{description}{limit_text}"
                ),
                inline=True
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command()
    async def buy(
        self,
        interaction: discord.Interaction,
        pack: app_commands.Transform[Pack, PackTransform],
        amount: int = 1,
    ):
        """
        Buy packs with your coins.

        Parameters
        ----------
        pack: Pack
            The pack you want to buy
        amount: int
            Number of packs to buy (default: 1)
        """
        if amount < 1:
            await interaction.response.send_message("Amount must be at least 1!", ephemeral=True)
            return
        
        if amount > 100:
            await interaction.response.send_message("You can only buy up to 100 packs at a time!", ephemeral=True)
            return
        
        if interaction.user.id in _active_operations:
            await interaction.response.send_message("You have another operation in progress!", ephemeral=True)
            return
        
        _active_operations.add(interaction.user.id)
        try:
            total_cost = pack.price * amount
            
            player, _ = await Player.get_or_create(discord_id=interaction.user.id)
            
            if player.coins < total_cost:
                await interaction.response.send_message(
                    f"You don't have enough coins! You need **{total_cost:,}** coins but only have **{player.coins:,}** coins.",
                    ephemeral=True
                )
                return
            
            emoji = pack.emoji + " " if pack.emoji else ""
            
            embed = discord.Embed(
                title="Confirm Purchase",
                description=(
                    f"Are you sure you want to buy **{amount}x {emoji}{pack.name}** "
                    f"for **{total_cost:,}** coins?"
                ),
                color=discord.Color.blue()
            )
            
            view = ConfirmView(interaction.user)
            await interaction.response.send_message(embed=embed, view=view)
            
            await view.wait()
            
            if view.value is None:
                embed.description = "Purchase timed out."
                embed.color = discord.Color.greyple()
                await interaction.edit_original_response(embed=embed, view=None)
                return
            
            if not view.value:
                embed.description = "Purchase cancelled."
                embed.color = discord.Color.red()
                await interaction.edit_original_response(embed=embed, view=None)
                return
            
            async with in_transaction():
                await player.refresh_from_db()
                
                if player.coins < total_cost:
                    embed.description = "You no longer have enough coins!"
                    embed.color = discord.Color.red()
                    await interaction.edit_original_response(embed=embed, view=None)
                    return
                
                player.coins -= total_cost
                await player.save(update_fields=["coins"])
                
                player_pack, created = await PlayerPack.get_or_create(
                    player=player,
                    pack=pack,
                    defaults={"quantity": 0}
                )
                player_pack.quantity += amount
                await player_pack.save(update_fields=["quantity"])
            
            embed.title = "Purchase Complete!"
            embed.description = (
                f"You bought **{amount}x {emoji}{pack.name}**!\n"
                f"Coins spent: **{total_cost:,}**\n"
                f"New balance: **{player.coins:,}** coins\n"
                f"You now have **{player_pack.quantity}** of this pack."
            )
            embed.color = discord.Color.green()
            await interaction.edit_original_response(embed=embed, view=None)
        finally:
            _active_operations.discard(interaction.user.id)

    @app_commands.command()
    async def inventory(self, interaction: discord.Interaction):
        """
        View your owned packs.
        """
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        player_packs = await PlayerPack.filter(player=player, quantity__gt=0).prefetch_related("pack")
        
        if not player_packs:
            await interaction.response.send_message(
                "You don't own any packs! Use `/pack buy` to purchase some.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="Your Packs",
            description="",
            color=discord.Color.gold()
        )
        
        for pp in player_packs:
            emoji = pp.pack.emoji + " " if pp.pack.emoji else ""
            embed.description += f"{emoji}**{pp.pack.name}**: {pp.quantity}\n"
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command()
    async def give(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        pack: app_commands.Transform[PlayerPack, OwnedPackTransform],
        amount: int = 1,
    ):
        """
        Give packs to another user.

        Parameters
        ----------
        user: discord.User
            The user you want to give packs to
        pack: PlayerPack
            The pack you want to give
        amount: int
            Number of packs to give (default: 1)
        """
        if user.id == interaction.user.id:
            await interaction.response.send_message("You cannot give packs to yourself!", ephemeral=True)
            return
        
        if user.bot:
            await interaction.response.send_message("You cannot give packs to bots!", ephemeral=True)
            return
        
        if amount < 1:
            await interaction.response.send_message("Amount must be at least 1!", ephemeral=True)
            return
        
        if amount > pack.quantity:
            await interaction.response.send_message(
                f"You only have **{pack.quantity}** of this pack!",
                ephemeral=True
            )
            return
        
        if interaction.user.id in _active_operations:
            await interaction.response.send_message("You have another operation in progress!", ephemeral=True)
            return
        
        await pack.fetch_related("pack", "player")
        the_pack = pack.pack
        
        _active_operations.add(interaction.user.id)
        try:
            async with in_transaction():
                await pack.refresh_from_db()
                
                if pack.quantity < amount:
                    await interaction.response.send_message(
                        f"You no longer have enough packs!",
                        ephemeral=True
                    )
                    return
                
                pack.quantity -= amount
                await pack.save(update_fields=["quantity"])
                
                recipient, _ = await Player.get_or_create(discord_id=user.id)
                
                recipient_pack = await PlayerPack.filter(player=recipient, pack=the_pack).first()
                if recipient_pack:
                    recipient_pack.quantity += amount
                    await recipient_pack.save(update_fields=["quantity"])
                else:
                    await PlayerPack.create(
                        player=recipient,
                        pack=the_pack,
                        quantity=amount
                    )
            
            emoji = the_pack.emoji + " " if the_pack.emoji else ""
            await interaction.response.send_message(
                f"{interaction.user.mention} gave **{amount}x {emoji}{the_pack.name}** to {user.mention}!\n"
                f"You now have **{pack.quantity}** of this pack."
            )
        finally:
            _active_operations.discard(interaction.user.id)

    @app_commands.command()
    async def open(
        self,
        interaction: discord.Interaction,
        pack: app_commands.Transform[PlayerPack, OwnedPackTransform],
        amount: int = 1,
    ):
        """
        Open your owned packs to get NBAs.

        Parameters
        ----------
        pack: PlayerPack
            The pack you want to open
        amount: int
            Number of packs to open (default: 1)
        """
        if amount < 1:
            await interaction.response.send_message("Amount must be at least 1!", ephemeral=True)
            return
        
        if amount > 10:
            await interaction.response.send_message(
                "You can only open up to 10 packs at a time!",
                ephemeral=True
            )
            return
        
        if interaction.user.id in _active_operations:
            await interaction.response.send_message(
                "You have another pack operation in progress! Please wait.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer()
        
        _active_operations.add(interaction.user.id)
        try:
            await pack.fetch_related("pack", "pack__special", "player")
            the_pack = pack.pack
            player = pack.player
            
            async with in_transaction():
                await pack.refresh_from_db()
                
                if pack.quantity < amount:
                    await interaction.followup.send(
                        f"You only have **{pack.quantity}** of this pack!"
                    )
                    return
                
                if the_pack.daily_limit > 0:
                    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    opens_today = await PackOpenHistory.filter(
                        player=player,
                        pack=the_pack,
                        opened_at__gte=today_start
                    ).count()
                    
                    remaining = the_pack.daily_limit - opens_today
                    if remaining <= 0:
                        hours_until_reset = 24 - timezone.now().hour
                        await interaction.followup.send(
                            f"You've reached the daily limit for opening **{the_pack.name}**!\n"
                            f"Your limit will reset in about {hours_until_reset} hours."
                        )
                        return
                    
                    if amount > remaining:
                        await interaction.followup.send(
                            f"You can only open **{remaining}** more of this pack today!"
                        )
                        return
                
                pack.quantity -= amount
                await pack.save(update_fields=["quantity"])
                
                special_to_use = None
                if the_pack.special_id:
                    special_to_use = the_pack.special
                
                if the_pack.special_only and special_to_use:
                    special_balls = await BallInstance.filter(
                        special=special_to_use,
                        deleted=False
                    ).prefetch_related("ball").distinct().values_list("ball_id", flat=True)
                    
                    available_balls = await Ball.filter(
                        enabled=True,
                        id__in=list(special_balls),
                        rarity__gte=the_pack.min_rarity,
                        rarity__lte=the_pack.max_rarity
                    ).all()
                    
                    if not available_balls:
                        available_balls = await Ball.filter(
                            enabled=True,
                            rarity__gte=the_pack.min_rarity,
                            rarity__lte=the_pack.max_rarity
                        ).all()
                else:
                    available_balls = await Ball.filter(
                        enabled=True,
                        rarity__gte=the_pack.min_rarity,
                        rarity__lte=the_pack.max_rarity
                    ).all()
                
                if not available_balls:
                    pack.quantity += amount
                    await pack.save(update_fields=["quantity"])
                    await interaction.followup.send(
                        f"No {settings.plural_collectible_name} available in this pack's rarity range!"
                    )
                    return
                
                total_rarity = sum(b.rarity for b in available_balls)
                
                results = []
                
                for _ in range(amount):
                    pack_cards = []
                    for _ in range(the_pack.cards_count):
                        roll = random.uniform(0, total_rarity)
                        cumulative = 0
                        selected_ball = available_balls[0]
                        
                        for ball in available_balls:
                            cumulative += ball.rarity
                            if roll <= cumulative:
                                selected_ball = ball
                                break
                        
                        attack_bonus = random.randint(-settings.max_attack_bonus, settings.max_attack_bonus)
                        health_bonus = random.randint(-settings.max_health_bonus, settings.max_health_bonus)
                        
                        instance = await BallInstance.create(
                            ball=selected_ball,
                            player=player,
                            attack_bonus=attack_bonus,
                            health_bonus=health_bonus,
                            special=special_to_use,
                            server_id=interaction.guild_id if interaction.guild else None,
                        )
                        pack_cards.append(instance)
                        results.append(instance)
                    
                    await PackOpenHistory.create(
                        player=player,
                        pack=the_pack,
                        ball_received=pack_cards[0] if pack_cards else None
                    )
            
            emoji = the_pack.emoji + " " if the_pack.emoji else ""
            
            if len(results) == 1:
                inst = results[0]
                ball = inst.countryball
                attack = "{:+}".format(inst.attack_bonus)
                health = "{:+}".format(inst.health_bonus)
                special_text = f" ({inst.specialcard.name})" if inst.specialcard else ""
                
                embed = discord.Embed(
                    title=f"{emoji}{the_pack.name}",
                    description=(
                        f"{interaction.user.mention} You packed **{ball.country}**!{special_text}\n"
                        f"(#{inst.pk:0X}, {attack}%/{health}%)"
                    ),
                    color=discord.Color.gold()
                )
                
                ball_emoji = self.bot.get_emoji(ball.emoji_id)
                if ball_emoji:
                    embed.set_thumbnail(url=ball_emoji.url)
            else:
                description = f"{interaction.user.mention} You opened **{amount}x {the_pack.name}**!\n\n"
                for inst in results:
                    ball = inst.countryball
                    attack = "{:+}".format(inst.attack_bonus)
                    health = "{:+}".format(inst.health_bonus)
                    special_text = f" ({inst.specialcard.name})" if inst.specialcard else ""
                    ball_emoji = self.bot.get_emoji(ball.emoji_id)
                    emoji_str = str(ball_emoji) + " " if ball_emoji else ""
                    description += f"{emoji_str}**{ball.country}**{special_text} (#{inst.pk:0X}, {attack}%/{health}%)\n"
                
                embed = discord.Embed(
                    title=f"{emoji}{the_pack.name} Results",
                    description=description,
                    color=discord.Color.gold()
                )
            
            await interaction.followup.send(embed=embed)
        finally:
            _active_operations.discard(interaction.user.id)
