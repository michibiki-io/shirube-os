"""Expose fixed guest failure classifications without retaining console content."""
import re


class GuestCommandError(RuntimeError):
    def __init__(self, status, output):
        output=output.replace('\r\n','\n')
        self.diagnostics = {'exit_status': int(status)}
        kinds = re.findall(r'(?m)^(ValueError|RuntimeError|PermissionError|FileNotFoundError|OSError|TypeError|CalledProcessError):', output)
        if kinds:
            self.diagnostics['exception_type'] = kinds[-1]
        errno=re.search(r'\[Errno ([0-9]{1,3})\]', output)
        if errno:self.diagnostics['errno']=int(errno[1])
        super().__init__('Private guest command failed (exit '+str(int(status))+')')
