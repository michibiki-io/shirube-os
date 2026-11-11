#!/usr/bin/env python3
"""Compute policy from individual commit messages supplied as a JSON array."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from producer.policy import next_version

parser=argparse.ArgumentParser();parser.add_argument('--previous')
args=parser.parse_args()
print(next_version(args.previous,json.load(sys.stdin)))
