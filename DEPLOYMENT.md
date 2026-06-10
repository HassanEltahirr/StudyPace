# Permanent Deployment

StudyPace is now packaged as one Docker web service:

- FastAPI serves `/api`, `/health`, and `/ready`.
- FastAPI also serves the built React app for `/`, `/login`, `/courses`, `/calendar`, and every other frontend route.
- User data, uploaded slides, and rendered slide images live under `backend/data`.

## Recommended First Launch: Render

The repo includes `render.yaml`, so Render can create the web service from a
Blueprint.

1. Push this folder to a GitHub repo.
2. In Render, choose **New > Blueprint**.
3. Pick the GitHub repo.
4. Render will create a Docker web service named `studypace`.
5. Use a paid web service with the included persistent disk. Without the disk,
   user accounts/uploads can disappear after redeploys or restarts.
6. After deploy, open the Render URL.
7. If Render gives a different URL than `https://studypace.onrender.com`, update
   `CORS_ORIGINS` in Render's environment settings to the real URL and redeploy.

Optional:

```text
ANTHROPIC_API_KEY=<rotated key>
GOOGLE_CLIENT_ID=<google oauth client id>
GOOGLE_ALLOWED_DOMAIN=<school domain>
DATABASE_URL=<supabase postgres pooled connection string>
STUDYPACE_STORAGE_BACKEND=r2
R2_ACCOUNT_ID=<cloudflare account id>
R2_ACCESS_KEY_ID=<r2 access key>
R2_SECRET_ACCESS_KEY=<r2 secret key>
R2_BUCKET=studypace-uploads
```

## Why One Container

This avoids a fragile split deployment where a static frontend has to know the
backend URL. Students use one URL, and browser requests to `/api` stay same-origin.

## Required Production Checks

Before sharing the app with students:

```bash
cd backend
python scripts/check_production_readiness.py
```

The deployed service should also pass:

```text
GET /health
GET /ready
```

## Data Warning

This setup is good for a real beta or private launch. For public beta traffic,
set `DATABASE_URL` to Supabase Postgres and configure Cloudflare R2. If those
secrets are missing, the app falls back to SQLite plus local disk, which is not
the final architecture for many concurrent students.
