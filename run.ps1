# SAHAL System - Development Run Script for Windows PowerShell
# This script sets up and runs the Flask development server

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "SAHAL System - Flask Development Server" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if virtual environment exists
if (-Not (Test-Path "venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv venv
    Write-Host "Virtual environment created." -ForegroundColor Green
}

# Activate virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
& ".\venv\Scripts\Activate.ps1"

# Check if dependencies are installed
Write-Host "Checking dependencies..." -ForegroundColor Yellow
pip install -q -r requirements.txt
Write-Host "Dependencies installed/updated." -ForegroundColor Green

# Create .env file if it doesn't exist
if (-Not (Test-Path ".env")) {
    Write-Host "Creating .env file from template..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host ".env file created. Please update it with your configuration." -ForegroundColor Yellow
}

# Create uploads directory if it doesn't exist
if (-Not (Test-Path "uploads")) {
    New-Item -ItemType Directory -Path "uploads" -Force > $null
    Write-Host "Created uploads directory." -ForegroundColor Green
}

Write-Host ""
Write-Host "Starting Flask development server..." -ForegroundColor Green
Write-Host "Server running at: http://localhost:5000" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host ""

# Run Flask app
python app.py
