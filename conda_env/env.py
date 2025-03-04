# -*- coding: utf-8 -*-
# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
from __future__ import absolute_import, print_function

from collections import OrderedDict
from itertools import chain
import os
import re
import json

from conda.base.context import context
from conda.exceptions import EnvironmentFileEmpty, EnvironmentFileNotFound
from conda.cli import common  # TODO: this should never have to import form conda.cli
from conda.common.compat import odict
from conda.common.serialize import yaml_safe_load, yaml_safe_dump
from conda.core.prefix_data import PrefixData
from conda.gateways.connection.download import download_text
from conda.gateways.connection.session import CONDA_SESSION_SCHEMES
from conda.models.enums import PackageType
from conda.models.match_spec import MatchSpec
from conda.models.prefix_graph import PrefixGraph
from conda.history import History

try:
    from tlz.itertoolz import concatv, groupby
except ImportError:  # pragma: no cover
    from conda._vendor.toolz.itertoolz import concatv, groupby  # NOQA


VALID_KEYS = ('name', 'dependencies', 'prefix', 'channels', 'variables')


def validate_keys(data, kwargs):
    """Check for unknown keys, remove them and print a warning."""
    invalid_keys = []
    new_data = data.copy() if data else {}
    for key in data.keys():
        if key not in VALID_KEYS:
            invalid_keys.append(key)
            new_data.pop(key)

    if invalid_keys:
        filename = kwargs.get('filename')
        verb = 'are' if len(invalid_keys) != 1 else 'is'
        plural = 's' if len(invalid_keys) != 1 else ''
        print("\nEnvironmentSectionNotValid: The following section{plural} on "
              "'{filename}' {verb} invalid and will be ignored:"
              "".format(filename=filename, plural=plural, verb=verb))
        for key in invalid_keys:
            print(' - {}'.format(key))
        print('')

    deps = data.get('dependencies', [])
    depsplit = re.compile(r"[<>~\s=]")
    is_pip = lambda dep: 'pip' in depsplit.split(dep)[0].split('::')
    lists_pip = any(is_pip(_) for _ in deps if not isinstance(_, dict))
    for dep in deps:
        if (isinstance(dep, dict) and 'pip' in dep and not lists_pip):
            print("Warning: you have pip-installed dependencies in your environment file, "
                  "but you do not list pip itself as one of your conda dependencies.  Conda "
                  "may not use the correct pip to install your packages, and they may end up "
                  "in the wrong place.  Please add an explicit pip dependency.  I'm adding one"
                  " for you, but still nagging you.")
            new_data['dependencies'].insert(0, 'pip')
            break
    return new_data


def load_from_directory(directory):
    """Load and return an ``Environment`` from a given ``directory``"""
    files = ['environment.yml', 'environment.yaml']
    while True:
        for f in files:
            try:
                return from_file(os.path.join(directory, f))
            except EnvironmentFileNotFound:
                pass
        old_directory = directory
        directory = os.path.dirname(directory)
        if directory == old_directory:
            break
    raise EnvironmentFileNotFound(files[0])


# TODO tests!!!
def from_environment(name, prefix, no_builds=False, ignore_channels=False, from_history=False):
    """
        Get environment object from prefix
    Args:
        name: The name of environment
        prefix: The path of prefix
        no_builds: Whether has build requirement
        ignore_channels: whether ignore_channels
        from_history: Whether environment file should be based on explicit specs in history

    Returns:     Environment object
    """
    # requested_specs_map = History(prefix).get_requested_specs_map()
    pd = PrefixData(prefix, pip_interop_enabled=True)
    variables = pd.get_environment_env_vars()

    if from_history:
        history = History(prefix).get_requested_specs_map()
        deps = [str(package) for package in history.values()]
        return Environment(name=name, dependencies=deps, channels=list(context.channels),
                           prefix=prefix, variables=variables)

    precs = tuple(PrefixGraph(pd.iter_records()).graph)
    grouped_precs = groupby(lambda x: x.package_type, precs)
    conda_precs = sorted(concatv(
        grouped_precs.get(None, ()),
        grouped_precs.get(PackageType.NOARCH_GENERIC, ()),
        grouped_precs.get(PackageType.NOARCH_PYTHON, ()),
    ), key=lambda x: x.name)

    pip_precs = sorted(concatv(
        grouped_precs.get(PackageType.VIRTUAL_PYTHON_WHEEL, ()),
        grouped_precs.get(PackageType.VIRTUAL_PYTHON_EGG_MANAGEABLE, ()),
        grouped_precs.get(PackageType.VIRTUAL_PYTHON_EGG_UNMANAGEABLE, ()),
        # grouped_precs.get(PackageType.SHADOW_PYTHON_EGG_LINK, ()),
    ), key=lambda x: x.name)

    if no_builds:
        dependencies = ['='.join((a.name, a.version)) for a in conda_precs]
    else:
        dependencies = ['='.join((a.name, a.version, a.build)) for a in conda_precs]
    if pip_precs:
        dependencies.append({'pip': ["%s==%s" % (a.name, a.version) for a in pip_precs]})

    channels = list(context.channels)
    if not ignore_channels:
        for prec in conda_precs:
            canonical_name = prec.channel.canonical_name
            if canonical_name not in channels:
                channels.insert(0, canonical_name)
    return Environment(name=name, dependencies=dependencies, channels=channels, prefix=prefix,
                       variables=variables)


def from_yaml(yamlstr, **kwargs):
    """Load and return a ``Environment`` from a given ``yaml string``"""
    data = yaml_safe_load(yamlstr)
    filename = kwargs.get("filename")
    if data is None:
        raise EnvironmentFileEmpty(filename)
    data = validate_keys(data, kwargs)

    if kwargs is not None:
        for key, value in kwargs.items():
            data[key] = value

    return Environment(**data)


def from_file(filename):
    url_scheme = filename.split("://", 1)[0]
    if url_scheme in CONDA_SESSION_SCHEMES:
        yamlstr = download_text(filename)
    elif not os.path.exists(filename):
        raise EnvironmentFileNotFound(filename)
    else:
        with open(filename, 'rb') as fp:
            yamlb = fp.read()
            try:
                yamlstr = yamlb.decode('utf-8')
            except UnicodeDecodeError:
                yamlstr = yamlb.decode('utf-16')
    return from_yaml(yamlstr, filename=filename)


# TODO test explicitly
class Dependencies(OrderedDict):
    def __init__(self, raw, *args, **kwargs):
        super(Dependencies, self).__init__(*args, **kwargs)
        self.raw = raw
        self.parse()

    def parse(self):
        if not self.raw:
            return

        self.update({'conda': []})

        for line in self.raw:
            if isinstance(line, dict):
                self.update(line)
            else:
                self['conda'].append(common.arg2spec(line))

        if 'pip' in self:
            if not self['pip']:
                del self['pip']
            if not any(MatchSpec(s).name == 'pip' for s in self['conda']):
                self['conda'].append('pip')

    # TODO only append when it's not already present
    def add(self, package_name):
        self.raw.append(package_name)
        self.parse()


def unique(seq, key=None):
    """ Return only unique elements of a sequence
    >>> tuple(unique((1, 2, 3)))
    (1, 2, 3)
    >>> tuple(unique((1, 2, 1, 3)))
    (1, 2, 3)
    Uniqueness can be defined by key keyword
    >>> tuple(unique(['cat', 'mouse', 'dog', 'hen'], key=len))
    ('cat', 'mouse')
    """
    seen = set()
    seen_add = seen.add
    if key is None:
        for item in seq:
            if item not in seen:
                seen_add(item)
                yield item
    else:  # calculate key
        for item in seq:
            val = key(item)
            if val not in seen:
                seen_add(val)
                yield item


class Environment(object):
    def __init__(self, name=None, filename=None, channels=None,
                 dependencies=None, prefix=None, variables=None):
        self.name = name
        self.filename = filename
        self.prefix = prefix
        self.dependencies = Dependencies(dependencies)
        self.variables = variables

        if channels is None:
            channels = []
        self.channels = channels

    def add_channels(self, channels):
        self.channels = list(unique(chain.from_iterable((channels, self.channels))))

    def remove_channels(self):
        self.channels = []

    def to_dict(self, stream=None):
        d = odict([('name', self.name)])
        if self.channels:
            d['channels'] = self.channels
        if self.dependencies:
            d['dependencies'] = self.dependencies.raw
        if self.variables:
            d['variables'] = self.variables
        if self.prefix:
            d['prefix'] = self.prefix
        if stream is None:
            return d
        stream.write(json.dumps(d))

    def to_yaml(self, stream=None):
        d = self.to_dict()
        out = yaml_safe_dump(d, stream)
        if stream is None:
            return out

    def save(self):
        with open(self.filename, "wb") as fp:
            self.to_yaml(stream=fp)
