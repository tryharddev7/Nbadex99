from django.contrib import admin
from django.utils.safestring import mark_safe

from ..models import Pack, PackOpenHistory, PlayerPack


@admin.register(Pack)
class PackAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "emoji_display",
        "price",
        "cards_count",
        "rarity_range",
        "daily_limit",
        "enabled",
    ]
    list_editable = ["price", "daily_limit", "enabled"]
    list_filter = ["enabled", "created_at"]
    search_fields = ["name", "description"]
    ordering = ["price"]
    
    fieldsets = [
        (
            None,
            {
                "fields": [
                    "name",
                    "description",
                    "emoji",
                    "price",
                    "cards_count",
                ],
            },
        ),
        (
            "Rarity Settings",
            {
                "description": "Configure the rarity range for cards in this pack",
                "fields": [
                    "min_rarity",
                    "max_rarity",
                ],
            },
        ),
        (
            "Limits",
            {
                "description": "Configure purchase limits",
                "fields": [
                    "daily_limit",
                ],
            },
        ),
        (
            "Special Event",
            {
                "description": "Link pack to a special event",
                "fields": [
                    "special",
                    "special_only",
                ],
            },
        ),
        (
            "Status",
            {
                "fields": [
                    "enabled",
                ],
            },
        ),
    ]
    autocomplete_fields = ["special"]

    @admin.display(description="Emoji")
    def emoji_display(self, obj: Pack) -> str:
        return obj.emoji or "-"

    @admin.display(description="Rarity")
    def rarity_range(self, obj: Pack) -> str:
        return f"{obj.min_rarity} - {obj.max_rarity}"


@admin.register(PlayerPack)
class PlayerPackAdmin(admin.ModelAdmin):
    list_display = ["player", "pack", "quantity"]
    list_filter = ["pack"]
    search_fields = ["player__discord_id"]
    autocomplete_fields = ["player", "pack"]


@admin.register(PackOpenHistory)
class PackOpenHistoryAdmin(admin.ModelAdmin):
    list_display = ["player", "pack", "opened_at", "cards_received"]
    list_filter = ["pack", "opened_at"]
    search_fields = ["player__discord_id"]
    ordering = ["-opened_at"]
    readonly_fields = ["player", "pack", "opened_at", "cards_received"]
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
