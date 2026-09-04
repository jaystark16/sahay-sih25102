FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files
COPY . .

# Expose port (Render sets the PORT environment variable)
EXPOSE 10000

# Run the FastAPI server
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}
