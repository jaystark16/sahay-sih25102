import os
import sys

# Add the project root to sys.path so we can import from main.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
