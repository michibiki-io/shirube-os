#!/usr/bin/python3
"""Keep Salt runtime APIs while excluding private-key test/example payloads."""
import argparse
import base64
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil

ORIGINAL='fc48476e5ad95c98537171cf86b709a60772263a9f935ad1809632381f83f78f'
PATCHED='3a9717617f17fe2b3676b360b7e702870d749182c2d36d0b79f30a383bfa8b6c'
MARKER='# Modified by michibiki-io for Shirube OS: incomplete private-key examples redacted.\n'


def examples(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Salt key-module source is missing or linked')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()==PATCHED:return
    if hashlib.sha256(data).hexdigest()!=ORIGINAL:raise ValueError('Unexpected pinned Salt key-module source')
    text=data.decode();pattern=r"'priv': '-----BEGIN RSA PRIVATE KEY-----.*?-----END RSA PRIVATE KEY-----'"
    found=list(re.finditer(pattern,text,re.S))
    if len(found)!=2 or any('...' not in match[0] for match in found):raise ValueError('Unexpected Salt documentation examples')
    text=MARKER+re.sub(pattern,"'priv': '<private key omitted from example>'",text,flags=re.S)
    compile(text,str(path),'exec');path.write_text(text)


def runtime(root):
    root=Path(root).resolve();site=root/'lib/python3.11/site-packages'
    if root==Path('/') or not site.is_dir():raise ValueError('Expected task-owned Salt runtime root')
    target=site/'Cryptodome/SelfTest'
    if target.is_symlink() or not target.resolve().is_relative_to(root):raise ValueError('Unsafe cryptographic test directory')
    if target.exists():shutil.rmtree(target)
    module=site/'salt/wheel/key.py'
    if not module.resolve().is_relative_to(root):raise ValueError('Salt module escaped its runtime')
    examples(module)
    for bytecode in (module.parent/'__pycache__').glob('key.*.pyc'):
        if bytecode.is_symlink() or not bytecode.resolve().is_relative_to(root):raise ValueError('Unsafe Salt bytecode path')
        bytecode.unlink()
    # Local build-directory URLs are not useful instance provenance. Public
    # source pins and complete corresponding sources are retained separately.
    for direct in site.glob('*.dist-info/direct_url.json'):
        if json.loads(direct.read_text()).get('url','').startswith('file:'):direct.unlink()
    for record in site.glob('*.dist-info/RECORD'):
        rows=[]
        for row in csv.reader(record.read_text().splitlines()):
            if len(row)!=3:raise ValueError('Unexpected installed Python RECORD')
            target=(site/row[0]).resolve()
            if not target.is_relative_to(root):raise ValueError('Python RECORD escaped its Salt runtime')
            if not target.is_file():continue
            if target==record.resolve():rows.append([row[0],'','']);continue
            data=target.read_bytes()
            rows.append([row[0],'sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='),str(len(data))])
        with record.open('w',newline='') as output:csv.writer(output,lineterminator='\n').writerows(rows)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['source','runtime']);parser.add_argument('root',type=Path)
    args=parser.parse_args()
    if args.mode=='source':examples(args.root/'salt/wheel/key.py')
    else:runtime(args.root)
