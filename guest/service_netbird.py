#!/usr/bin/python3
"""Commit handler for non-secret NetBird settings only."""
import sys
sys.path.insert(0, '/usr/libexec/shirube')
import netbird_control as control
from vyos import ConfigError
from vyos.config import Config


def get_config(config=None):
    conf = config if config is not None else Config()
    if not conf.exists(['service', 'netbird']):
        return None
    return dict(conf.get_config_dict(['service', 'netbird'], key_mangling=('-', '_'), get_first_key=True))


def verify(config):
    try:
        control.validate(config)
    except ValueError as error:
        raise ConfigError(str(error)) from None


def generate(config):
    pass


def apply(config):
    try:
        control.apply(config)
    except Exception:
        raise ConfigError('NetBird service update failed; secret details suppressed') from None


if __name__ == '__main__':
    try:
        value = get_config()
        verify(value)
        generate(value)
        apply(value)
    except ConfigError as error:
        print(error)
        sys.exit(1)
