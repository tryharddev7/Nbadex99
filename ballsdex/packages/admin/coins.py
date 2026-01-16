import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from tortoise.transactions import in_transaction

from ballsdex.core.models import Pack, Player, PlayerPack
from ballsdex.core.utils.logging import log_action
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.admin.coins")


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
        packs = await Pack.all().order_by("name")
        choices = []
        for pack in packs:
            if current.lower() in pack.name.lower():
                emoji = pack.emoji + " " if pack.emoji else ""
                choices.append(app_commands.Choice(
                    name=f"{emoji}{pack.name}",
                    value=str(pack.id)
                ))
        return choices[:25]


class CoinsAdmin(app_commands.Group):
    """
    Manage player coins
    """

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def add(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
        amount: int,
    ):
        """
        Add coins to a player.

        Parameters
        ----------
        user: discord.User
            The user to give coins to
        amount: int
            Number of coins to add
        """
        if amount <= 0:
            await interaction.response.send_message("Amount must be positive!", ephemeral=True)
            return

        player, _ = await Player.get_or_create(discord_id=user.id)
        old_balance = player.coins
        player.coins += amount
        await player.save(update_fields=["coins"])

        await interaction.response.send_message(
            f"Added **{amount:,}** coins to {user.mention}.\n"
            f"Balance: {old_balance:,} -> **{player.coins:,}** coins",
            ephemeral=True
        )
        
        await log_action(
            f"{interaction.user} added {amount:,} coins to {user} (ID: {user.id}). "
            f"New balance: {player.coins:,}",
            interaction.client,
        )

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def remove(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
        amount: int,
    ):
        """
        Remove coins from a player.

        Parameters
        ----------
        user: discord.User
            The user to remove coins from
        amount: int
            Number of coins to remove
        """
        if amount <= 0:
            await interaction.response.send_message("Amount must be positive!", ephemeral=True)
            return

        player, _ = await Player.get_or_create(discord_id=user.id)
        old_balance = player.coins
        player.coins = max(0, player.coins - amount)
        await player.save(update_fields=["coins"])

        await interaction.response.send_message(
            f"Removed **{amount:,}** coins from {user.mention}.\n"
            f"Balance: {old_balance:,} -> **{player.coins:,}** coins",
            ephemeral=True
        )
        
        await log_action(
            f"{interaction.user} removed {amount:,} coins from {user} (ID: {user.id}). "
            f"New balance: {player.coins:,}",
            interaction.client,
        )

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def set(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
        amount: int,
    ):
        """
        Set a player's coin balance to a specific amount.

        Parameters
        ----------
        user: discord.User
            The user to set coins for
        amount: int
            The new coin balance
        """
        if amount < 0:
            await interaction.response.send_message("Amount cannot be negative!", ephemeral=True)
            return

        player, _ = await Player.get_or_create(discord_id=user.id)
        old_balance = player.coins
        player.coins = amount
        await player.save(update_fields=["coins"])

        await interaction.response.send_message(
            f"Set {user.mention}'s coins to **{amount:,}**.\n"
            f"Balance: {old_balance:,} -> **{player.coins:,}** coins",
            ephemeral=True
        )
        
        await log_action(
            f"{interaction.user} set {user}'s (ID: {user.id}) coins to {amount:,}. "
            f"Previous balance: {old_balance:,}",
            interaction.client,
        )

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def check(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
    ):
        """
        Check a player's coin balance.

        Parameters
        ----------
        user: discord.User
            The user to check coins for
        """
        player, _ = await Player.get_or_create(discord_id=user.id)

        await interaction.response.send_message(
            f"{user.mention} has **{player.coins:,}** coins.",
            ephemeral=True
        )


class PacksAdmin(app_commands.Group):
    """
    Manage player packs
    """

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def add(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
        pack: app_commands.Transform[Pack, PackTransform],
        amount: int = 1,
    ):
        """
        Give packs to a player.

        Parameters
        ----------
        user: discord.User
            The user to give packs to
        pack: Pack
            The pack to give
        amount: int
            Number of packs to give (default: 1)
        """
        if amount <= 0:
            await interaction.response.send_message("Amount must be positive!", ephemeral=True)
            return

        async with in_transaction():
            player, _ = await Player.get_or_create(discord_id=user.id)
            
            player_pack = await PlayerPack.filter(player=player, pack=pack).first()
            if player_pack:
                old_quantity = player_pack.quantity
                player_pack.quantity += amount
                await player_pack.save(update_fields=["quantity"])
            else:
                old_quantity = 0
                player_pack = await PlayerPack.create(
                    player=player,
                    pack=pack,
                    quantity=amount
                )

        emoji = pack.emoji + " " if pack.emoji else ""
        await interaction.response.send_message(
            f"Added **{amount}x {emoji}{pack.name}** to {user.mention}.\n"
            f"Pack count: {old_quantity} -> **{player_pack.quantity}**",
            ephemeral=True
        )
        
        await log_action(
            f"{interaction.user} added {amount}x {pack.name} to {user} (ID: {user.id}). "
            f"New count: {player_pack.quantity}",
            interaction.client,
        )

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def remove(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
        pack: app_commands.Transform[Pack, PackTransform],
        amount: int = 1,
    ):
        """
        Remove packs from a player.

        Parameters
        ----------
        user: discord.User
            The user to remove packs from
        pack: Pack
            The pack to remove
        amount: int
            Number of packs to remove (default: 1)
        """
        if amount <= 0:
            await interaction.response.send_message("Amount must be positive!", ephemeral=True)
            return

        player, _ = await Player.get_or_create(discord_id=user.id)
        
        player_pack = await PlayerPack.filter(player=player, pack=pack).first()
        if not player_pack or player_pack.quantity <= 0:
            await interaction.response.send_message(
                f"{user.mention} doesn't have any {pack.name} packs!",
                ephemeral=True
            )
            return

        old_quantity = player_pack.quantity
        player_pack.quantity = max(0, player_pack.quantity - amount)
        await player_pack.save(update_fields=["quantity"])

        emoji = pack.emoji + " " if pack.emoji else ""
        await interaction.response.send_message(
            f"Removed **{amount}x {emoji}{pack.name}** from {user.mention}.\n"
            f"Pack count: {old_quantity} -> **{player_pack.quantity}**",
            ephemeral=True
        )
        
        await log_action(
            f"{interaction.user} removed {amount}x {pack.name} from {user} (ID: {user.id}). "
            f"New count: {player_pack.quantity}",
            interaction.client,
        )

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def check(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
    ):
        """
        Check a player's pack inventory.

        Parameters
        ----------
        user: discord.User
            The user to check packs for
        """
        player, _ = await Player.get_or_create(discord_id=user.id)
        player_packs = await PlayerPack.filter(player=player, quantity__gt=0).prefetch_related("pack")

        if not player_packs:
            await interaction.response.send_message(
                f"{user.mention} doesn't own any packs.",
                ephemeral=True
            )
            return

        pack_list = ""
        for pp in player_packs:
            emoji = pp.pack.emoji + " " if pp.pack.emoji else ""
            pack_list += f"{emoji}**{pp.pack.name}**: {pp.quantity}\n"

        await interaction.response.send_message(
            f"**{user.display_name}'s Packs:**\n{pack_list}",
            ephemeral=True
        )
