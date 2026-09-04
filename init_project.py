#!/usr/bin/env python
"""
SAHAL System - Project Initialization Script
Sets up the Flask project with required directories and files
"""

import os
import sys
from pathlib import Path

def create_directory(path):
    """Create directory if it doesn't exist"""
    Path(path).mkdir(parents=True, exist_ok=True)
    return path

def check_python_version():
    """Check if Python version is compatible"""
    if sys.version_info < (3, 7):
        print("❌ Python 3.7+ is required")
        sys.exit(1)
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor} detected")

def check_dependencies():
    """Check if required packages are installed"""
    required = ['flask', 'jinja2', 'werkzeug']
    missing = []
    
    for package in required:
        try:
            __import__(package)
            print(f"✅ {package} is installed")
        except ImportError:
            print(f"❌ {package} is NOT installed")
            missing.append(package)
    
    if missing:
        print(f"\n⚠️  Missing packages: {', '.join(missing)}")
        print("Run: pip install -r requirements.txt")
        return False
    return True

def setup_directories():
    """Create required directories"""
    directories = [
        'templates',
        'templates/components',
        'templates/public',
        'templates/auth',
        'templates/dashboard',
        'templates/errors',
        'static',
        'static/css',
        'static/js',
        'uploads',
        'logs',
    ]
    
    print("\n📁 Setting up directories...")
    for directory in directories:
        created = create_directory(directory)
        print(f"✅ {created}/")

def check_files():
    """Check if required files exist"""
    required_files = [
        'app.py',
        'config.py',
        'requirements.txt',
        '.gitignore',
        '.env.example',
        'templates/base.html',
        'static/css/main.css',
        'static/js/utils.js',
    ]
    
    print("\n📄 Checking required files...")
    missing = []
    for file in required_files:
        if os.path.exists(file):
            print(f"✅ {file}")
        else:
            print(f"❌ {file} - MISSING")
            missing.append(file)
    
    return len(missing) == 0

def create_env_file():
    """Create .env file from template if not exists"""
    if not os.path.exists('.env') and os.path.exists('.env.example'):
        print("\n⚙️  Creating .env file...")
        with open('.env.example', 'r') as src:
            with open('.env', 'w') as dst:
                dst.write(src.read())
        print("✅ .env file created (update with your configuration)")
    elif os.path.exists('.env'):
        print("\n✅ .env file already exists")
    else:
        print("\n⚠️  .env.example not found")

def main():
    """Run initialization"""
    print("=" * 50)
    print("SAHAL System - Project Initialization")
    print("=" * 50)
    
    # Check Python version
    check_python_version()
    
    # Setup directories
    setup_directories()
    
    # Check files
    print()
    files_ok = check_files()
    
    # Create .env
    create_env_file()
    
    # Check dependencies
    print()
    deps_ok = check_dependencies()
    
    # Summary
    print("\n" + "=" * 50)
    if files_ok and deps_ok:
        print("✅ Project initialization complete!")
        print("\nNext steps:")
        print("1. Update .env file with your configuration")
        print("2. Run: python app.py")
        print("3. Open: http://localhost:5000")
    else:
        print("⚠️  Project initialization has some issues")
        print("Please fix the above errors and try again")
    print("=" * 50)

if __name__ == '__main__':
    main()
