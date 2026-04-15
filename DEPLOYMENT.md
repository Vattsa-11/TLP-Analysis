# Railway Deployment Guide

## Prerequisites
- Railway account and project created
- GitHub repository connected to Railway

## Environment Variables Required

Add these environment variables in Railway dashboard under "Variables":

```
OCR_API_KEY=K81654833188957
```

Optional:
```
PORT=8000  # (Railway automatically assigns this)
HOST=0.0.0.0  # (defaults to 0.0.0.0)
```

## Deployment Steps

1. **Connect GitHub Repository**
   - Go to Railway dashboard
   - Click "New Project"
   - Select "Deploy from GitHub"
   - Choose the repository

2. **Configure Environment Variables**
   - Navigate to project settings
   - Add all required environment variables from above
   - Save

3. **Deploy**
   - Railway will automatically detect the Dockerfile
   - Build and deploy the container
   - Your app will be available at the Railway-provided URL

## Verification

After deployment:
- Check logs: Look for "Uvicorn running on" message
- Visit the app URL to confirm the UI loads
- Test upload functionality with sample PDFs
- Verify all API endpoints are accessible

## Troubleshooting

**Error: "Port already in use"**
- Railway automatically assigns available ports. This should not occur.

**Error: "OCR API Key missing"**
- Ensure OCR_API_KEY is set in Railway environment variables
- Without it, OCR fallback will not work for scanned PDFs

**Error: "Static files not found"**
- Dockerfile includes static/ directory
- If issue persists, verify static/index.html exists in repository

**Error: "Module not found"**
- All dependencies are pinned in requirements.txt
- Dockerfile installs all dependencies before running

## Architecture

- **Base Image**: python:3.9-slim
- **Framework**: FastAPI with Uvicorn
- **Port**: 8000 (Railway adjusts as needed via PORT env var)
- **Host**: 0.0.0.0 (accepts external connections)
- **Filesystem**: Ephemeral (files don't persist between deployments)
  - User uploads are processed in memory
  - Excel files are streamed directly to users

## File Deployment

Files included in deployment:
- Python source code (main.py, extractor.py, attendance_extractor.py)
- Static files (static/index.html and subdirectories)
- requirements.txt
- Dockerfile
- .env variables (set via Railway dashboard, not in repo)

Files NOT deployed (in .gitignore):
- Cache files (__pycache__, *.pyc)
- Old Excel reports and PDFs
- Debug logs
