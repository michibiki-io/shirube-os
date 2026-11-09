"""Resume interrupted live-build downloads without repeating compilation/bootstrap."""
from pathlib import Path
import subprocess
from .apt_archive import recover_install_failure


def attempt(command, cwd, env):
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    tail = ''
    for line in process.stdout:
        print(line, end='', flush=True)
        tail = (tail + line)[-1024 * 1024:]
    return process.wait(), tail


def build(command, *, cwd, env, lock=Path('/src/source-lock.json')):
    status, output = attempt(command, cwd, env)
    if not status:
        return
    root = Path(cwd)/'build'
    chroot = root/'chroot'
    if (chroot.is_symlink() or not (chroot/'var/lib/apt/lists').is_dir()
            or not (chroot/'var/cache/apt/archives').is_dir()
            or not recover_install_failure(output, lock, chroot)):
        raise subprocess.CalledProcessError(status, command)
    # The original command prepares and cleans the workspace. Resume lb itself
    # in that existing workspace so successful stages and downloads survive.
    resumed = ['lb', 'build']
    status, _ = attempt(resumed, root, env)
    if status:
        raise subprocess.CalledProcessError(status, resumed)
