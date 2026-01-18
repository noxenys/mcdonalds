FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (including tzdata for timezone support)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libpq-dev \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set timezone
ENV TZ=Asia/Shanghai

# Create a non-root user for Hugging Face Spaces security compliance
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .

# Expose port 7860 for Hugging Face Spaces / Koyeb health checks
EXPOSE 7860

# Default command (can be overridden by docker-compose)
CMD ["python", "bot.py"]
