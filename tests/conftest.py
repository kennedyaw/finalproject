import os
import sys

# Let tests import app.py, nasa.py and db.py from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
