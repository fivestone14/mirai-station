"""Put the viewstation dir on sys.path so `import server` resolves the same way
it does under launchd (server.py inserts its own dir for snapshot/pipeline)."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
