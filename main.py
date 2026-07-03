import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import gui


if __name__ == "__main__":
    gui.run()
