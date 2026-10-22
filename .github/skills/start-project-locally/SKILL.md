---
name: start-project-locally
description: Start the ProjectFinance Django application locally with SQLite and verify that the development server responds.
---

# Start ProjectFinance locally

Use this skill when asked to start or run ProjectFinance on the local machine.

## Procedure

1. Run commands from the repository root. Use the existing `venv\Scripts\python.exe` when present; otherwise use the user's configured Python interpreter. Do not activate or create an environment unless needed.
2. Use the local Django settings (`config.settings_local`), which select the persistent SQLite database at `db.sqlite3`. Do not start Docker or use production settings for this task.
3. In PowerShell, set the settings module in the process environment before invoking `manage.py`:

   ```powershell
   $env:DJANGO_SETTINGS_MODULE = 'config.settings_local'
   ```

   This project initializes OpenTelemetry before Django executes the requested management command. Setting the module only with `--settings` is too late for that initialization; without the environment variable the command can fail while loading `DJANGO_SECRET_KEY` from `.env.local`.
4. Check the local configuration:

   ```powershell
   & '.\venv\Scripts\python.exe' manage.py check
   ```

   If no project virtual environment exists, substitute the configured interpreter path. If checks fail, report the actual error; do not conceal it with fallback settings or silently change configuration.
5. Check whether `http://127.0.0.1:8000/` already responds. If it does, reuse the running server and do not launch a duplicate.
6. If the app is not already available, apply pending local database migrations:

   ```powershell
   & '.\venv\Scripts\python.exe' manage.py migrate
   ```

   Never delete, reset, or replace `db.sqlite3` as part of starting the app.
7. Start the Django development server bound to loopback on port 8000, leaving it running in an attached background process:

   ```powershell
   & '.\venv\Scripts\python.exe' manage.py runserver 127.0.0.1:8000
   ```

   If port 8000 is occupied by an unrelated service and the app is not responding there, use port 8001 instead and report the chosen URL. Do not terminate another process to free the port.
8. Verify the selected URL with an HTTP request and report the URL and status code. If it does not respond successfully, report the observed startup or request error instead of claiming the app is running.

Do not print or expose values from `.env.local` or other secret-bearing files. Do not install packages unless a required dependency is confirmed missing; if installation is necessary, use the project's declared requirements and report the change.
