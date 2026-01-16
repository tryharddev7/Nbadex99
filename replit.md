# BallsDex Discord Bot

## Overview

BallsDex is a Discord bot for collecting "countryballs" - collectible items that spawn in Discord servers. Users can catch, trade, and manage their collections. The project consists of two main components: a Discord bot built with discord.py and an admin panel built with Django.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Discord Bot (`ballsdex/`)
- **Framework**: discord.py with application commands (slash commands)
- **Database ORM**: Tortoise ORM for async database operations
- **Structure**: Modular cog-based architecture where features are organized into "packages"
  - `packages/balls/` - Core collection and inventory commands
  - `packages/admin/` - Administrative commands for bot management
  - `packages/trade/` - Trading system between players
  - `packages/betting/` - Betting system for NBAs
  - `packages/coins/` - Virtual currency and pack system
  - `packages/config/` - Server configuration commands
  - `packages/countryballs/` - Spawning logic for collectibles

### Admin Panel (`admin_panel/`)
- **Framework**: Django with custom admin site
- **Purpose**: Web-based administration for managing balls, players, trades, and blacklists
- **Authentication**: Discord OAuth2 via python-social-auth
- **Models**: Django models mirror Tortoise ORM models in `bd_models/` app
- **Image Generation**: Preview system for countryball card generation

### Data Models (Core entities)
- `Ball` - Collectible definitions (stats, artwork, rarity)
- `BallInstance` - Individual collected items owned by players
- `Player` - User accounts with privacy/donation policies
- `Trade` / `TradeObject` - Trading system records
- `Special` - Limited-time event modifiers for balls
- `Regime` / `Economy` - Visual themes for ball backgrounds
- `Pack` - Purchasable card packs with virtual currency
- `GuildConfig` - Per-server bot configuration

### Configuration
- Settings loaded from `config.yml` (YAML format)
- JSON schema reference available in `json-config-ref.json`
- Environment variables for database URL (`BALLSDEXBOT_DB_URL` or `DATABASE_URL`)

## External Dependencies

### Database
- **PostgreSQL** - Primary database (required)
- Connection via Tortoise ORM (bot) and Django ORM (admin panel)
- Database URL format: `postgres://user:password@host:port/dbname`

### External Services
- **Discord API** - Bot functionality and OAuth2 authentication
- **Prometheus** - Metrics collection (optional, configurable)
- **Sentry** - Error tracking (optional, via sentry_sdk)
- **Discord Webhooks** - Admin notifications from the panel

### Key Python Dependencies
- `discord.py` - Discord bot framework
- `tortoise-orm` - Async ORM for the bot
- `Django` - Admin panel framework
- `Pillow` - Image generation for collectible cards
- `aiohttp` - Async HTTP client
- `pyyaml` - Configuration parsing
- `poetry` - Dependency management

### Infrastructure (Docker)
- Docker Compose setup available for PostgreSQL and Redis
- Separate containers for bot and admin panel