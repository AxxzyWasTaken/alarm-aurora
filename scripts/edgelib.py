"""The ALARM Edge channel: an `[aurora-edge]` overlay ahead of `[aurora]`.

ALARM is rolling: [core], [extra] and [alarm] move every day, and Aurora's
Testing userspace moves with them. The Edge overlay therefore carries only the
kernel and Mesa built from one qualified aurora-edge pair; every other package
comes from [aurora] (Testing), exactly as Testing users get it. The overlay
moves only when the aurora-distributions Edge train that names it is adopted.
"""
import re

TAG = re.compile(r'edge-[0-9]{8}\.[1-9][0-9]*')
REVISION = re.compile(r'[0-9a-f]{40}')
FEED = 'https://aurorasilicon.org/alarm-aurora/edge/aarch64'
TESTING_FEED = 'https://aurorasilicon.org/alarm-aurora/testing/aarch64'
RELEASES = 'https://github.com/aurora-silicon/alarm-aurora/releases/download/'
DATABASE = 'aurora-edge'
# Recipes (pkgbase) built from the pair, by family; each family has one version.
FAMILIES = {'kernel': ('linux-aurora',),
            'mesa': ('mesa-aurora', 'mesa-aurora-fex-guest', 'aurora-flatpak-gl')}
STAGING_PATHS = ('sources.json',) + tuple('packages/' + base + '/' for bases in FAMILIES.values() for base in bases)
SECTION = '[{name}]\nSigLevel = Required DatabaseRequired\nServer = {server}\n\n'


def snapshot(tag):
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        raise ValueError('invalid immutable Edge snapshot tag: %r' % (tag,))
    return tag


def family(pkgbase):
    return next((key for key, bases in FAMILIES.items() if pkgbase in bases), None)


def select(rows):
    """(pkgbase, pkgname, version) rows of an Edge build: the overlay's package names."""
    versions, builds, names = {}, {}, []
    for pkgbase, pkgname, version in rows:
        kind = family(pkgbase)
        if kind is None:
            raise ValueError('not a kernel or Mesa package: ' + pkgname)
        # One recipe builds one full version; the recipes of a family share pkgver.
        builds.setdefault(pkgbase, set()).add(version)
        versions.setdefault(kind, set()).add(version.rsplit('-', 1)[0])
        names.append(pkgname)
    if (set(versions) != set(FAMILIES) or any(len(found) != 1 for found in versions.values())
            or any(len(found) != 1 for found in builds.values())):
        raise ValueError('the Edge build must carry one version of the kernel and one of Mesa')
    if len(set(names)) != len(names):
        raise ValueError('duplicate Edge package')
    return sorted(names)


def check_staging_files(paths):
    """An edge-staging branch differs from main only in the pair pins and their recipes."""
    outside = [path for path in paths if path != 'sources.json'
               and not any(path.startswith(prefix) for prefix in STAGING_PATHS[1:])]
    if outside:
        raise ValueError('edge staging changes files outside the kernel and Mesa recipes: ' + ', '.join(outside))


def source(name, revision, tree):
    if not REVISION.fullmatch(revision or '') or not REVISION.fullmatch(tree or ''):
        raise ValueError('invalid %s revision or tree' % name)
    return {'repository': 'aurora-silicon/' + name, 'branch': 'aurora-edge', 'revision': revision, 'tree': tree}


def packaged_sources(sources, trees):
    mesa = {sources[name]['commit'] for name in ('mesa-aurora', 'mesa-aurora-fex-guest', 'aurora-flatpak-gl')
            if name in sources}
    if len(mesa) != 1:
        raise ValueError('the Edge build must package one Mesa revision')
    return {'linux': source('linux', sources['linux-aurora']['commit'], trees['linux']),
            'mesa': source('mesa', mesa.pop(), trees['mesa'])}


def edge_gate(record, tag, sources):
    """Serve on Edge only the overlay the adopted aurora-distributions Edge train names."""
    entry = ((record or {}).get('distributions') or {}).get('arch') or {}
    if (not record or record.get('schema_version') != 1
            or entry.get('repository') != 'aurora-silicon/alarm-aurora' or entry.get('snapshot') != snapshot(tag)):
        raise ValueError(f'{tag} is not the ALARM snapshot of the adopted Edge train')
    if (sources or {}).get('schema') != 1 or sources.get('channel') != 'edge' \
            or sources.get('packaged_sources') != record.get('pair'):
        raise ValueError(f'{tag} packaged another Linux/Mesa pair than the adopted Edge train')


def add_sections(text, testing_tag, edge_tag=None):
    """Build-time pacman.conf: Aurora's immutable releases ahead of [core]."""
    at = text.index('[core]')
    sections = ''
    if edge_tag is not None:
        sections += SECTION.format(name=DATABASE, server=RELEASES + snapshot(edge_tag))
    sections += SECTION.format(name='aurora', server=RELEASES + testing_tag)
    sections += '[asahi-alarm]\nServer = https://github.com/asahi-alarm/asahi-alarm/releases/download/$arch\n\n'
    return text[:at] + sections + text[at:]


def seal(text, testing_tag, edge_tag=None, channel='testing'):
    """After assembly the installed system follows the feeds, never the build releases."""
    text = text.replace(RELEASES + testing_tag + '\n',
                        f'https://aurorasilicon.org/alarm-aurora/{"testing" if edge_tag else channel}/aarch64\n')
    if edge_tag is not None:
        text = text.replace(RELEASES + snapshot(edge_tag) + '\n', FEED + '\n')
    return text
