# Use official lightweight Python image
FROM python:3.10-slim

# Install FFmpeg (Required for image, video, and audio conversions) and clean up apt cache
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot.py .

# Command to run the bot
CMD ["python", "bot.py"]
