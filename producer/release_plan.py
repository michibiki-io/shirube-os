"""Semantic publication/validation policy. Never imported by image build recipes.

Roles describe consumers, not whether a change can alter binary bytes. Extend a
role with consumer evidence and positive, negative and mutation tests. Unknown
roles hold publication and request candidate validation; they never allocate.
"""
import hashlib
import json
from pathlib import PurePosixPath, Path
import re

POLICY_VERSION = 1
CANDIDATE_ROLES = {'product', 'build', 'acceptance', 'unknown'}
PRODUCT_FILES = {'source-lock.json', 'LICENSE', 'NOTICE', 'license-audit.json',
                 'shirube-os-logo.svg', 'containers/native/packages.json',
                 'containers/native/external-sources.lock.json',
                 'containers/native/rubygems.lock.json', 'containers/native/image.py'}
# These modules generate installed product behavior, rather than run verification.
PRODUCT_MODULES = {'branding', 'cloud_init', 'cloud_init_guest', 'image_templates',
                   'installer_admin', 'netbird', 'netbird_guest', 'sflow'}
# Naming families are an extension contract, not an exemption for all producer code.
BUILD_PREFIXES = ('compile_', 'compiler_', 'native_', 'staged', 'stage_', 'component_',
                  'library_cache', 'kernel_compile_cache', 'apt_archive', 'workspace',
                  'resources', 'architecture', 'recipes', 'packages', 'debootstrap',
                  'livebuild_', 'snapshot_fetch', 'strip_policy')
ACCEPTANCE_PREFIXES = ('source', 'security_', 'public_test_keys', 'signing', 'collect',
                       'pipeline', 'release', 'integration', 'reconstruction', 'notices',
                       'package_notices', 'embedded_notices', 'factory_audit', 'guest',
                       'sanitize', 'erasure', 'bundles', 'disks', 'vm', 'boot_media',
                       'cargo_', 'go_', 'vpp_', 'salt_', 'debian_', 'runtime_sources',
                       'http_rust_', 'netbird_sources', 'netbird_fixture', 'network_',
                       'bash_source', 'charset_source', 'geoip', 'kbd_', 'libxcrypt_',
                       'ocaml_', 'opentracing_')
CONTROL_FILES = {'producer/actions.py', 'producer/recover_release.py',
                 '.github/workflows/release.yml', '.github/workflows/recover-release.yml'}


def role(path, consumers=()):
    """First match wins; product/legal/consumed fixtures precede test/docs roles."""
    p = PurePosixPath(path)
    if path in PRODUCT_FILES or path.startswith(('guest/', 'patches/', 'third_party/', 'LICENSES/')):
        return 'product'
    if p.parent.as_posix() == 'producer' and p.stem in PRODUCT_MODULES and p.suffix == '.py':
        return 'product'
    if path in consumers:
        return 'build'
    if path in CONTROL_FILES or re.fullmatch(r'producer/release_(?:plan|evidence)\.py', path):
        return 'control'
    if path.startswith('tests/fixtures/'):
        return 'acceptance'
    if re.fullmatch(r'tests/(?:test_[^/]+|[^/]+_fixture)\.py', path):
        return 'test'
    if path == 'README.md' or (path.startswith('docs/') and p.suffix == '.md' and not path.startswith('docs/evidence/')):
        return 'docs'
    if (path == '.github/workflows/test.yml' or path.startswith(('scripts/check-workflow', 'scripts/check_workflow'))
            or path in {'.gitignore', '.gitattributes', '.dockerignore'}):
        # .dockerignore changes the compiler context, so it is a build dependency.
        return 'build' if path in {'.dockerignore', '.gitattributes'} else 'ci'
    if path.startswith('.github/workflows/') and p.suffix in {'.yml', '.yaml'}:
        return 'build'
    if path.startswith(('containers/', 'scripts/')):
        return 'build'
    if path.startswith(('schemas/', 'examples/', 'docs/evidence/')):
        return 'acceptance'
    if p.parent.as_posix() == 'producer' and p.suffix == '.py':
        stem = p.stem
        if stem in PRODUCT_MODULES:
            return 'product'
        if stem.startswith(BUILD_PREFIXES):
            return 'build'
        if stem.startswith(ACCEPTANCE_PREFIXES):
            return 'acceptance'
        if stem in {'__init__', 'cli', 'runner_probe', 'native_trial'}:
            return 'build'
    return 'unknown'


def classify(paths, consumers=()):
    roles = {}
    for path in sorted(set(paths)):
        selected = role(path, consumers)
        roles.setdefault(selected, []).append(path)
    publication = 'hold' if 'unknown' in roles else 'publish' if 'product' in roles else 'none'
    validation = 'candidate' if set(roles) & CANDIDATE_ROLES else 'focused'
    return {'schema_version': POLICY_VERSION, 'publication': publication,
            'validation': validation, 'roles': roles,
            'reason': 'unknown-role' if publication == 'hold' else
                      'product-input' if publication == 'publish' else 'no-product-input',
            'prior_guest_evidence': 'NOT_REUSED',
            'invalidation_reasons': [r+'-dependency-changed' for r in sorted(set(roles) & CANDIDATE_ROLES)]}


def declared_consumers(project):
    """Compile declarations override docs/test families, including new members.

    Publication controls are deliberately outside the compile graph. Broad
    project-source.tar inclusion is attribution, not an executable consumer.
    Malformed declarations fail rather than remove a consumer override.
    """
    result = set()
    for file in sorted((Path(project)/'containers/native').glob('inputs-*.json')):
        data = json.loads(file.read_text())
        for key in ('roots', 'templates'):
            result.update(data.get(key, []))
        for node, edges in data.get('graph', {}).items():
            result.add(node); result.update(edges)
    return result


def plan(paths, commit, *, consumers=(), run='', attempt='', inputs=None):
    result = classify(paths, consumers)
    result.update(source_commit=commit, run_id=run, run_attempt=attempt,
                  policy_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  dependency_changes=inputs or {}, candidate_only=result['publication'] != 'publish',
                  deferred=result['publication'] == 'hold')
    result['gates'] = {'focused': 'required-but-unrun',
                       'candidate': 'required-but-unrun' if result['validation'] == 'candidate' else 'skipped-not-required',
                       'aggregate': 'required-but-unrun' if result['validation'] == 'candidate' else 'skipped-not-required',
                       'publication': 'required-but-unrun' if result['publication'] == 'publish' else 'skipped-not-required'}
    return result


def outcomes(plan, results):
    """No success-by-skipped-publish: missing required work always fails."""
    result = dict(plan); result['gates'] = dict(plan['gates'])
    failed = []
    for gate, status in result['gates'].items():
        if status == 'skipped-not-required':
            continue
        actual = results.get(gate)
        result['gates'][gate] = ('passed' if actual == 'success' else
                                 'failed' if actual in {'failure', 'cancelled'} else 'required-but-unrun')
        if actual != 'success': failed.append(gate)
    result['status'] = 'failed' if failed else 'validated-publication-held' if plan['publication'] == 'hold' else 'passed-required-scope'
    return result, not failed
