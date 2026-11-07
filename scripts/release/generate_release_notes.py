#!/usr/bin/env python3
"""Render individual commit subjects/bodies/SHAs; PR titles are not inputs."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from producer.policy import release_notes
print(release_notes(json.load(sys.stdin)),end='')
