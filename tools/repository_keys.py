#!/usr/bin/env python3
"""List only local signing keys referenced by enabled, signature-checked repos."""
import configparser
from pathlib import Path
import sys
from urllib.parse import unquote, urlparse


def referenced_keys(text):
    config = configparser.ConfigParser(interpolation=None, strict=False)
    # DNF decorates its otherwise INI-compatible dump with repo banners.
    config.read_string('\n'.join(line for line in text.splitlines() if not line.startswith('===')))
    keys = set()
    for section in config.sections():
        if section == 'main' or not config.getboolean(section, 'enabled', fallback=True):
            continue
        if not config.getboolean(section, 'gpgcheck', fallback=False):
            raise ValueError(f'Signature checking disabled for {section}')
        if not config.getboolean(section, 'sslverify', fallback=True):
            raise ValueError(f'TLS verification disabled for {section}')
        urls = config.get(section, 'gpgkey', fallback='').split()
        if not urls:
            raise ValueError(f'No signing key for {section}')
        for value in urls:
            url = urlparse(value)
            path = Path(unquote(url.path))
            if url.scheme != 'file' or url.netloc or '..' in path.parts or path.parent != Path('/etc/pki/rpm-gpg'):
                raise ValueError(f'Key for {section} is not a package-owned local key: {value}')
            keys.add(str(path))
    if not keys:
        raise ValueError('No enabled repositories with verifiable local keys')
    return sorted(keys)


if __name__ == '__main__':
    print('\n'.join(referenced_keys(Path(sys.argv[1]).read_text())))
