#!/usr/bin/env python3
"""Refuse an [aurora-edge] overlay that does not close over the served [aurora] Testing feed.

Edge installs list [aurora-edge] ahead of [aurora]. libalpm takes a package
from the first repository that has its name, and resolves a depend from the
first repository whose package satisfies it. So:

- every depend, conflict and provide of an overlay package that names an
  Aurora package must be met by the overlay or by Testing (a kernel needing an
  avd-fw newer than Testing serves breaks every `pacman -Syu`);
- every Testing package that depends on an overlay name must be satisfied by
  the overlay's version, or libalpm silently installs Testing's copy;
- no Testing package may conflict with an overlay package.

Names neither feed carries (glibc, kmod, ...) come from Arch Linux ARM and are
left to the feed's own depends check. The rules match libalpm's: a package of
that name, or a versioned provides, at a version alpm_vercmp accepts.

    check-edge-closure.py --overlay aurora-edge.db.tar.gz --testing aurora.db.tar.gz
"""
import argparse
import io
from pathlib import Path
import re
import sys
import tarfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import updatelib as lib  # noqa: E402

RELATION = re.compile(r'([^<>=]+)(?:(>=|<=|=|<|>)(.+))?')


def parse(text):
    match = RELATION.fullmatch(text)
    if not match:
        raise ValueError(f'unreadable relation: {text!r}')
    return match.groups()


def version_ok(have, operator, want):
    result = lib.alpm_vercmp(have, want)
    return {'>=': result >= 0, '<=': result <= 0, '=': result == 0, '<': result < 0, '>': result > 0}[operator]


def satisfies(package, relation):
    name, operator, want = relation
    if package['name'] == name and (operator is None or version_ok(package['version'], operator, want)):
        return True
    for provided, provided_operator, version in map(parse, package['provides']):
        if provided == name and (operator is None or (provided_operator == '=' and version_ok(version, operator, want))):
            return True
    return False


def names(package):
    return {package['name'], *(parse(text)[0] for text in package['provides'])}


def problems(overlay, testing):
    """Human-readable closure failures; empty when the overlay is safe to serve beside Testing."""
    found = []
    overlay_names = {name for package in overlay for name in names(package)}
    shadowed = {package['name'] for package in overlay}
    visible = [package for package in testing if package['name'] not in shadowed]
    carried = overlay_names | {name for package in testing for name in names(package)}
    for package in overlay:
        for text in package['depends']:
            relation = parse(text)
            if relation[0] not in carried:
                continue
            if relation[0] in overlay_names:
                if not any(satisfies(other, relation) for other in overlay):
                    found.append(f'{package["name"]} needs {text}, which [aurora-edge] does not meet')
            elif not any(satisfies(other, relation) for other in visible):
                found.append(f'{package["name"]} needs {text}, which Testing does not serve')
    for package in visible:
        for text in package['depends']:
            relation = parse(text)
            if relation[0] in overlay_names and not any(satisfies(other, relation) for other in overlay):
                found.append(f'Testing {package["name"]} needs {text}: [aurora-edge] does not meet it, '
                             'so pacman would install the Testing copy')
        for text in package['conflicts']:
            relation = parse(text)
            for other in overlay:
                if satisfies(other, relation):
                    found.append(f'Testing {package["name"]} conflicts with {text}: [aurora-edge] '
                                 f'serves {other["name"]} {other["version"]}')
    for package in overlay:
        for text in package['conflicts']:
            relation = parse(text)
            for other in visible:
                if satisfies(other, relation):
                    found.append(f'{package["name"]} conflicts with {text}: Testing serves '
                                 f'{other["name"]} {other["version"]}')
    return found


def database(data):
    """Packages of a pacman database archive (desc and, in older databases, depends files)."""
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
        for member in archive:
            directory, _, filename = member.name.rpartition('/')
            if not member.isfile() or filename not in ('desc', 'depends'):
                continue
            fields, key = entries.setdefault(directory, {}), None
            for line in archive.extractfile(member).read().decode().splitlines():
                if line.startswith('%') and line.endswith('%'):
                    key = line.strip('%')
                    fields[key] = []
                elif line and key:
                    fields[key].append(line)
    return [{'name': fields['NAME'][0], 'version': fields['VERSION'][0],
             'depends': fields.get('DEPENDS', []), 'conflicts': fields.get('CONFLICTS', []),
             'provides': fields.get('PROVIDES', [])} for fields in entries.values()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--overlay', type=Path, required=True)
    parser.add_argument('--testing', type=Path, required=True)
    args = parser.parse_args()
    found = problems(database(args.overlay.read_bytes()), database(args.testing.read_bytes()))
    for line in found:
        print(line, file=sys.stderr)
    if found:
        raise SystemExit('The [aurora-edge] overlay does not close over the served Testing feed.')
    print('[aurora-edge] closes over the served Testing feed.')


if __name__ == '__main__':
    main()
