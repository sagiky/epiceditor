FROM python:3.12-slim

# Install Java, wget, and clean up
RUN apt-get update && \
    apt-get install -y default-jre-headless wget && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. Create the defaults folder so the Python script can find the files
RUN mkdir -p defaults

# 2. Download the files directly into the defaults folder.
# CRITICAL: If your repo is private, GitHub links will cause an 'exit code: 8' crash. 
# Use Dropbox links ending in ?dl=1 if your repo is private!
RUN wget -q -O defaults/Epic.ipa "https://github.com/sagiky/epiceditor/releases/download/almost-default/Epic.ipa"
RUN wget -q -O defaults/Epic.apk "https://github.com/sagiky/epiceditor/releases/download/default/Epic.apk"

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your code into the container
COPY . .

# Make sure you uploaded the Linux version of this tool!
RUN if [ -f abe_multitool ]; then chmod +x abe_multitool; fi

EXPOSE 5000

# Start with Gunicorn to fix the warning and prevent instant crashes
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "300", "server:app"]