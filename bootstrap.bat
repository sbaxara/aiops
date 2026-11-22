@echo off
rem Copyright (c) 2026 Axara Pty Ltd.
rem SPDX-License-Identifier: AGPL-3.0-or-later
setlocal enabledelayedexpansion
rem One-shot environment bootstrap (Windows). Windows twin of bootstrap.sh:
rem   - ensures .env + strong SECRET_KEY / MASTER_ENCRYPTION_KEY
rem   - creates a self-signed TLS certificate when certs\server.crt is missing
rem   - brings up datastores, waits for Postgres
rem   - applies migrations (python manage.py migrate) + seeds the first admin
rem   - starts server + dashboard
rem Idempotent. Admin password via ADMIN_PASSWORD env var or secure prompt.

cd /d "%~dp0monitoring-server"

where docker >nul 2>&1 || (echo ERROR: docker not found & exit /b 1)
docker compose version >nul 2>&1 || (echo ERROR: docker compose v2 required & exit /b 1)

rem --- .env + secrets (shared helper) ---
python ensure_env.py || (echo ERROR: ensure_env.py failed & exit /b 1)

rem --- admin credentials ---
if "%ADMIN_USERNAME%"=="" set "ADMIN_USERNAME=admin"
if "%ADMIN_EMAIL%"=="" set "ADMIN_EMAIL=%ADMIN_USERNAME%@local"
if "%ADMIN_PASSWORD%"=="" (
  for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$s=Read-Host -AsSecureString 'Set a password for admin user ''%ADMIN_USERNAME%'''; [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))"`) do set "ADMIN_PASSWORD=%%p"
)
if "%ADMIN_PASSWORD%"=="" (echo ERROR: admin password required & exit /b 1)

rem --- TLS certificate (self-signed unless you supply certs\server.crt + .key) ---
if not exist certs\server.crt goto makecert
if not exist certs\server.key goto makecert
goto certok
rem makecert: create a self-signed certificate with the server image (manage.py make-cert).
:makecert
echo Creating a self-signed TLS certificate in monitoring-server\certs (replace it with your own for production)...
if not exist certs mkdir certs
if "%CERT_HOSTS%"=="" set "CERT_HOSTS=%COMPUTERNAME%"
docker compose run --rm --no-deps -v "%CD%\certs:/certs" -e CERT_HOSTS monitoring-server python manage.py make-cert || exit /b 1
rem certok: certificate present; continue.
:certok

rem --- datastores first ---
echo Starting datastores (postgres, chromadb, ollama)...
docker compose up -d postgres chromadb ollama || exit /b 1

if "%DB_USER%"=="" set "DB_USER=monitoring"
echo Waiting for Postgres...
set /a _tries=0
rem waitpg: poll pg_isready every 2 s, up to 60 tries, then jump to pgready.
:waitpg
docker compose exec -T postgres pg_isready -U %DB_USER% >nul 2>&1
if not errorlevel 1 goto pgready
set /a _tries+=1
if %_tries% geq 60 (echo ERROR: timeout waiting for Postgres & exit /b 1)
timeout /t 2 /nobreak >nul
goto waitpg
rem pgready: Postgres accepts connections; continue with migrations.
:pgready
echo Postgres ready.

rem --- migrate + seed admin in a one-off app container ---
echo Applying migrations and seeding admin...
docker compose run --rm -e ADMIN_USERNAME -e ADMIN_EMAIL -e ADMIN_PASSWORD -e ADMIN_RESET_PASSWORD monitoring-server bash -lc "python manage.py migrate && python manage.py seed-admin" || exit /b 1

rem --- start everything ---
echo Starting server + dashboard...
docker compose up -d || exit /b 1

echo.
echo [OK] Environment ready.
echo      Admin user : %ADMIN_USERNAME%
echo      Dashboard  : https://localhost  (or your configured host / ALLOWED_ORIGINS)
echo.
echo Air-gapped note: verify the LLM model is present -
echo      docker compose exec ollama ollama list    (expect gemma4:26b)
endlocal
