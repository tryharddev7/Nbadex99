# BallsDex Discord Bot

## Overview

BallsDex is a Discord bot for collecting "countryballs" - collectible items that spawn in Discord channels. Users can catch, trade, and manage their collections. The project consists of two main components: a Discord bot built with discord.py and an admin panel built with Django for content management.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Bot Architecture
- **Framework**: discord.py with async/await patterns
- **ORM**: Tortoise ORM for async database operations in the bot
- **Package Structure**: Modular cog-based design under `ballsdex/packages/`
  - `admin/` - Admin slash commands for bot management
  - `balls/` - Core countryball inventory and interaction commands
  - `betting/` - Player-to-player betting system
  - `coins/` - Virtual currency and pack purchasing system
  - `config/` - Server configuration commands
  - `countryballs/` - Spawning mechanics (referenced but not shown)
  - `trade/` - Trading system between players

### Admin Panel Architecture
- **Framework**: Django 5.x with custom admin site
- **Authentication**: Discord OAuth2 via python-social-auth, with role-based access tied to Discord server roles
- **ORM**: Django ORM with models mirroring Tortoise models in `bd_models/`
- **Media Handling**: Uploaded images stored in `./admin_panel/media/`
- **Image Generation**: Preview system renders countryball cards on-demand using PIL

### Data Models
Key models include:
- `Ball` - Countryball definitions (stats, images, rarity)
- `BallInstance` - Individual caught countryballs owned by players
- `Player` - User profiles with privacy/donation policies
- `Special` - Limited-time event modifiers for balls
- `Regime/Economy` - Visual customization categories
- `Trade/TradeObject` - Trading history
- `Pack/PlayerPack` - Purchasable card packs with virtual currency
- `GuildConfig` - Per-server bot settings
- `BlacklistedID/BlacklistedGuild` - Moderation blocklists

### Configuration
- Bot settings loaded from `config.yml` (YAML format with JSON schema reference in `json-config-ref.json`)
- Database URL from environment variable `DATABASE_URL` or `BALLSDEXBOT_DB_URL`
- Settings dataclass in `ballsdex/settings.py` provides typed access to configuration

## External Dependencies

### Database
- **PostgreSQL** - Primary database for both bot and admin panel
- Connection URL supports both `postgres://` and `postgresql://` schemes (auto-converted for Tortoise compatibility)

### Discord Integration
- Discord bot token required in config
- Discord OAuth2 for admin panel authentication
- Webhook notifications for admin actions

### Monitoring
- **Prometheus** - Metrics collection via `/metrics` endpoint
- **Sentry** - Error tracking integration (optional)

### Key Python Dependencies
- `discord.py` - Discord API wrapper
- `tortoise-orm` - Async ORM for bot
- `Django` - Admin panel framework
- `Pillow (PIL)` - Image generation for countryball cards
- `aiohttp` - Async HTTP client
- `pyyaml` - Configuration parsing

### Development Tools
- Poetry for dependency management
- Docker Compose for local development (PostgreSQL + Redis)
- Pre-commit hooks for code quality