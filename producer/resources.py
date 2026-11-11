"""Choose bounded compiler concurrency from actual container CPU/RAM limits."""
import math
import os
from pathlib import Path

GIB=1024**3


def profile(cpus=None,memory=None):
    if cpus is None:
        cpus=len(os.sched_getaffinity(0))
        quota=Path('/sys/fs/cgroup/cpu.max')
        if quota.is_file():
            amount,period=quota.read_text().split()
            if amount!='max':cpus=min(cpus,max(1,math.floor(int(amount)/int(period))))
    if memory is None:
        limit=Path('/sys/fs/cgroup/memory.max')
        value=limit.read_text().strip() if limit.is_file() else 'max'
        if value!='max':memory=int(value)
        else:
            memory=next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()
                        if line.startswith('MemAvailable:'))
    if type(cpus) is not int or cpus<1 or type(memory) is not int or memory<256*1024**2:
        raise ValueError('Invalid native compilation resource budget')
    cpus=min(cpus,64)
    cpp=max(1,min(cpus,memory//(1536*1024**2)))
    jobs=max(1,min(cpus,memory//GIB))
    return {'cpu_limit':cpus,'memory_limit_bytes':memory,'compile_jobs':cpp,'kernel_jobs':jobs,
            'go_jobs':jobs,'go_memory_limit_mib':max(256,min(1536,memory//1024**2//(jobs+1)))}


def environment(base=None):
    result=dict(os.environ if base is None else base);budget=profile()
    jobs=str(budget['compile_jobs'])
    result.update(MAKEFLAGS='-j'+jobs,MAKE_PARALLEL_JOBS=jobs,OPAMJOBS=jobs,
                  CMAKE_BUILD_PARALLEL_LEVEL=jobs,CARGO_BUILD_JOBS=jobs,
                  DEB_BUILD_OPTIONS='parallel='+jobs,DPKG_DEB_THREADS_MAX=jobs,
                  GOMAXPROCS=str(budget['cpu_limit']),GOFLAGS='-p='+str(budget['go_jobs']),
                  GOMEMLIMIT=str(budget['go_memory_limit_mib'])+'MiB')
    return result
