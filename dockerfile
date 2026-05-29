FROM python:3.12-slim

# Install Java (for apktool + uber-apk-signer).
RUN apt-get update && \
    apt-get install -y default-jre-headless && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything else
COPY . .

# Make the Linux abe_multitool executable
RUN if [ -f abe_multitool ]; then chmod +x abe_multitool; fi

EXPOSE 5000
CMD ["python", "server.py"]