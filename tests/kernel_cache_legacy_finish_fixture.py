def finish(*, state=STATE):
    if not Path(state).exists():
        return
    data = json.loads(Path(state).read_text())
    env = environment(data)
    subprocess.run(['ccache', '--cleanup'], env=env, check=True)
    stats = subprocess.check_output(['ccache', '--print-stats'], env=env, text=True)
    report = {'scope': data['scope'], 'namespace': data['namespace'], 'restore': data['status'],
              'statistics': dict(line.split() for line in stats.splitlines())}
    report['export'] = export(data['store'], Path(data['mount']) / 'export.tar', data['scope'])
    (Path(data['mount']) / 'metrics.json').write_text(json.dumps(report, sort_keys=True) + '\n')
