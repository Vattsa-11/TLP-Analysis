# Use official Python runtime as base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (Railway will assign dynamically via PORT env variable)
EXPOSE 8000

# Run FastAPI with uvicorn using shell to expand PORT env variable
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
