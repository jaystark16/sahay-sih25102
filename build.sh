#!/usr/bin/env bash
# exit on error
set -o errexit

# 1. Build the React frontend
echo "Building the frontend..."
cd frontend
npm install
npm run build
cd ..

# 2. Move the built frontend to the static directory
echo "Moving built files to static directory..."
rm -rf static
mv frontend/dist static

# 3. Install Python dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt
