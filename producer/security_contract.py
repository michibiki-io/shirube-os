"""Shared private-key detection contract for binary, source and transfer audits."""
import re

# A format delimiter is also ordinary text in SSH/key-parser source and ELF
# executables. Require following armored key bytes, including encrypted PEM
# headers, so those programs remain buildable without allowing actual keys.
SECRET = re.compile(
    rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----[ \t]*'
    rb'(?:\r?\n|\\n)(?:(?:Proc-Type|DEK-Info):[^\r\n]{1,256}\r?\n)*'
    rb'(?:\r?\n)*[A-Za-z0-9/+]{32}')
