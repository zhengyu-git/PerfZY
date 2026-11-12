import sys
from pathlib import Path

# Tests import the package straight from the working tree, without requiring an
# editable install first.
sys.path.insert(0, str(Path(__file__).resolve().parent))
