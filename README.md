# AdSyncPro Backend

AdSyncPro is a specialized platform for YouTube creators and businesses to monitor ad performance and audience retention metrics.

## Tech Stack
- **Python**: 3.12 (standard for modern FastAPI apps)
- **Framework**: FastAPI (0.115+)
- **ORM**: SQLAlchemy (2.0+) with Async support
- **Migrations**: Alembic
- **Database**: PostgreSQL (16+)
- **Authentication**: Custom Auth + Google OAuth 2.0 (YouTube API)

## Main Packages
- `fastapi`: API Framework
- `uvicorn`: ASGI Server
- `sqlalchemy[asyncio]`: Database ORM
- `asyncpg`: Async PostgreSQL Driver
- `google-api-python-client`: YouTube Data & Analytics API
- `cryptography`: Token encryption (Fernet)
- `passlib`: Password hashing (Bcrypt)

## Database Models
- `User`: Handles accounts (Creators, Business Owners, Visitors).
- `Campaign`: Collections of YouTube videos owned by a User.
- `VideoMetric`: Stores video metadata, ad timestamps, and OAuth status.
- `DailyStat`: Historical tracking of views and retention.

## Running with Docker

1. **Environment Setup**:
   Ensure you have a `.env` file and `gcp_client_secret.json` in the root directory.

2. **Build and Run**:
   ```bash
   docker-compose up --build
   ```

3. **Database Migrations**:
   The containers run `alembic upgrade head` on startup to ensure the schema is current.

4. **API Documentation**:
   Once running, visit [http://localhost:8000/docs](http://localhost:8000/docs) for the Interactive Swagger UI.

## Local Development (Windows)

1. Activate the virtualenv.
2. Run migrations using the venv Python (avoids global-package conflicts):
   ```bash
   .venv\\Scripts\\python.exe -m alembic upgrade head
   ```
3. Start the API:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```
