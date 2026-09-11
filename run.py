#!/usr/bin/env python3
"""Entry point for the tool bot."""
import os
import sys
import logging

# Ensure bot.py is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    from bot import main
    main()
