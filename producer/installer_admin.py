"""Keep native installation tied to the enrolled instance administrator."""
from pathlib import Path
import ast

AUTHENTICATION = '''def enrolled_administrator():
    """Read saved instance authentication without exposing it in diagnostics."""
    try:
        administrator = environ.get('SUDO_USER', '')
        enrolled = Path(f'{DIR_CONFIG}/shirube-enrolled').read_text().strip()
        if (not re.fullmatch(r'[-_a-zA-Z0-9.]{1,100}', administrator) or administrator == 'root'
                or not re.fullmatch(r'[-_a-zA-Z0-9.]{1,100}', enrolled) or enrolled == 'root'):
            raise ValueError()
        config = ConfigTree(read_file(f'{DIR_CONFIG}/config.boot'))
        auth = ['system', 'login', 'user', administrator, 'authentication']
        path = auth+['encrypted-password']
        password_hash = config.return_value(path) if config.exists(path) else ''
        keys = auth+['public-keys']
        has_key = config.exists(keys) and any(config.return_value(keys+[key, 'key'])
            and config.return_value(keys+[key, 'type']) for key in config.list_nodes(keys))
        if (not password_hash or password_hash.startswith(('!', '*'))) and not has_key:
            raise ValueError()
        return administrator, password_hash
    except Exception:
        raise RuntimeError('Install from the saved instance administrator session.') from None


def configure_authentication(config_file: str, password_hash: str, administrator: str) -> None:
    """Retain the saved administrator; never introduce a separate fixed user."""
    try:
        config = ConfigTree(read_file(config_file))
        if password_hash and not password_hash.startswith(('!', '*')):
            config.set(['system', 'login', 'user', administrator, 'authentication', 'encrypted-password'],
                       value=password_hash, replace=True)
        else:
            keys = ['system', 'login', 'user', administrator, 'authentication', 'public-keys']
            saved = ConfigTree(read_file(f'{DIR_CONFIG}/config.boot'))
            if not saved.exists(keys) or not saved.list_nodes(keys):
                raise ValueError()
            for name in saved.list_nodes(keys):
                for field in ['key', 'type']:
                    value = saved.return_value(keys+[name, field])
                    if not value:
                        raise ValueError()
                    config.set(keys+[name, field], value=value, replace=True)
                if saved.exists(keys+[name, 'options']):
                    config.set(keys+[name, 'options'],
                               value=saved.return_value(keys+[name, 'options']), replace=True)
            config.set_tag(keys)
            # A factory configuration has no SSH service or console password.
            # Its retained key-only administrator still needs an access path.
            config.set(['service', 'ssh'])
        config.set_tag(['system', 'login', 'user'])
        with open(config_file, 'w') as target:
            target.write(config.to_string())
    except Exception:
        raise RuntimeError('Unable to retain saved administrator authentication.') from None


def preserve_live_configuration(selected_config: str, target_config_dir: str) -> None:
    """Keep persistent files when installing the active live configuration."""
    if Path(selected_config).resolve() == Path(f'{DIR_CONFIG}/config.boot').resolve():
        copytree(f'{DIR_CONFIG}/', target_config_dir, symlinks=True,
                 copy_function=copy_preserve_owner, dirs_exist_ok=True)

'''


def patch_installer(root):
    path = Path(root)/'usr/libexec/vyos/op_mode/image_installer.py'
    text = path.read_text()
    def replace(old, new):
        nonlocal text
        if text.count(old) != 1:
            raise ValueError('Unexpected native administrator installation contract')
        text = text.replace(old, new)
    replace('from os import environ\n', 'from os import environ\nimport re\n')
    start = text.index('def configure_authentication(')
    end = text.index('def validate_signature(', start)
    text = text[:start]+AUTHENTICATION+text[end:]
    start = text.index('    failed_check_status = ')
    end = text.index('    # ask for default console', start)
    text = text[:start]+'''    try:
        administrator, administrator_hash = enrolled_administrator()
    except RuntimeError as error:
        exit(str(error))
    print(f'The installed system will retain administrator "{administrator}" and its configured authentication.')

'''+text[end:]
    replace("                                 user_password)", "                                 administrator_hash, administrator)")
    replace("        copy(default_config, f'{target_config_dir}/config.boot')",
            "        preserve_live_configuration(default_config, target_config_dir)\n"
            "        copy(default_config, f'{target_config_dir}/config.boot')")
    replace("        setup_grub(DIR_DST_ROOT)", """        enrollment = Path(target_config_dir)/'shirube-enrolled'
        enrollment.write_text(administrator+'\\n')
        enrollment.chmod(0o600)

        setup_grub(DIR_DST_ROOT)""")
    for line in text.splitlines(True):
        if line.startswith(('MSG_INPUT_PASSWORD:', 'MSG_INPUT_PASSWORD_CONFIRM:')):
            text = text.replace(line, '')
    first, rest = text.split('\n', 1)
    text = first+'\n# Modified by michibiki-io for Shirube OS on 2026-11-11.\n'+rest
    ast.parse(text)
    path.write_text(text)
