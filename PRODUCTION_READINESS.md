# StudyPace Production Readiness

StudyPace can run as a private single-server production app once the backend
environment is configured safely.

For a permanent public launch, use the root `Dockerfile` and `render.yaml`.
They package the React frontend and FastAPI backend into one web service.

## Required Backend Environment

Copy `backend/.env.example` to `backend/.env`, then set:

```bash
STUDYPACE_ENV=production
SECRET_KEY=<unique random value, at least 32 characters>
CORS_ORIGINS=https://your-frontend-domain.example
STUDYPACE_LOCAL_ADMIN_ENABLED=false
STUDYPACE_DATA_DIR=/app/data
MAX_UPLOAD_MB=50
```

For a real multi-user beta, use Supabase Postgres instead of SQLite:

```bash
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-us-east-1.pooler.supabase.com:6543/postgres
```

Registered users are isolated into separate Postgres workspaces. The local
admin account still uses the default public workspace.

Use Cloudflare R2 for uploaded PDFs and generated slide images:

```bash
STUDYPACE_STORAGE_BACKEND=r2
R2_ACCOUNT_ID=<cloudflare-account-id>
R2_ACCESS_KEY_ID=<r2-access-key>
R2_SECRET_ACCESS_KEY=<r2-secret-key>
R2_BUCKET=studypace-uploads
R2_ENDPOINT_URL=https://<cloudflare-account-id>.r2.cloudflarestorage.com
```

Claude generation is optional:

```bash
ANTHROPIC_API_KEY=<your key>
```

If the Anthropic key was ever pasted into a chat, screenshot, log, or commit,
rotate it before launch.

## Frontend Environment

Vite embeds the backend URL at build time:

```bash
VITE_API_URL=https://your-api-domain.example
```

When using Docker Compose, pass it as a build arg through `.env`.

## Readiness Check

Run this before deploying:

```bash
cd backend
python scripts/check_production_readiness.py
```

The app also exposes:

```text
GET /health
GET /ready
```

`/health` confirms the process is alive. `/ready` confirms the database session
works.

## Data Privacy Model

Without Supabase, registered users are isolated into separate local workspaces:

```text
backend/data/user_workspaces/<username>/studypace_local.db
backend/data/user_workspaces/<username>/source_material/
backend/data/user_workspaces/<username>/slide_images/
```

The local admin account keeps using the default workspace:

```text
backend/data/studypace_local.db
backend/data/source_material/
backend/data/slide_images/
```

With Supabase/Postgres enabled, registered users are isolated by Postgres
workspace schemas. With R2 enabled, uploaded PDFs and slide images are stored
under per-user object prefixes instead of on the app server disk.
