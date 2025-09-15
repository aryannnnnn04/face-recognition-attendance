# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Install system-level dependencies needed for dlib and opencv
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libjpeg-dev \
    libgtk2.0-dev \
    && rm -rf /var/lib/apt/lists/*

# --- THE FINAL, UNIVERSAL MEMORY FIX ---
# Use the 'dd' command which is more compatible than 'fallocate'
RUN dd if=/dev/zero of=/swapfile bs=1M count=1024 && \
    chmod 600 /swapfile && \
    mkswap /swapfile && \
    swapon /swapfile

# Copy the requirements file into the container
COPY requirements.txt requirements.txt

# Install the Python dependencies
# This will now have enough memory to succeed
RUN pip install --no-cache-dir -r requirements.txt

# --- Clean up the swap file ---
RUN swapoff /swapfile && \
    rm /swapfile

# Copy the rest of the application code into the container
COPY . .

# Tell Docker that the container listens on port 5000
EXPOSE 5000

# Run the app using gunicorn
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5000", "app:app"]

