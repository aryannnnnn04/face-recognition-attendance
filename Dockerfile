# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Install system-level dependencies needed for dlib and opencv
# This is the key step that fixes the build failures
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libjpeg-dev \
    libgtk2.0-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt requirements.txt

# --- THE CRITICAL FIX ---
# Install the most memory-intensive packages one by one FIRST
# This allows the server to dedicate all resources to each hard task
RUN pip install --no-cache-dir dlib==19.24.2
RUN pip install --no-cache-dir opencv-python==4.8.1.78

# Now, install the rest of the (much smaller) packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Tell Docker that the container listens on port 5000
EXPOSE 5000

# Run the app using gunicorn
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5000", "app:app"]

