# STAGE 1: Define a stable base with a specific Python version
FROM python:3.9-slim-bullseye

# Set environment variables for a cleaner build
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory inside the container
WORKDIR /app

# STAGE 2: Install system-level dependencies
# This is critical for compiling dlib and opencv successfully.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libjpeg-dev \
    libgtk2.0-dev \
    # Clean up apt-get cache to keep the image small
    && rm -rf /var/lib/apt/lists/*

# STAGE 3: Create a swapfile to provide extra memory for the build
# This uses the 'dd' command, which is universally compatible, to prevent memory errors.
RUN dd if=/dev/zero of=/swapfile bs=1M count=1024 && \
    chmod 600 /swapfile && \
    mkswap /swapfile && \
    swapon /swapfile

# STAGE 4: Install Python dependencies
# Copy only the requirements file first to leverage Docker's layer caching.
COPY requirements.txt .
# The installation will now have enough memory to succeed.
RUN pip install --no-cache-dir -r requirements.txt

# STAGE 5: Clean up the swapfile after the heavy installation is done
RUN swapoff /swapfile && \
    rm /swapfile

# STAGE 6: Copy the application code into the container
COPY . .

# Expose the port the app runs on
EXPOSE 5000

# STAGE 7: Run the application using a production-ready server
# This command starts gunicorn to serve your Flask app.
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5000", "app:app"]
