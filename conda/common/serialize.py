# -*- coding: utf-8 -*-
# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
from __future__ import absolute_import, division, print_function, unicode_literals

from io import StringIO
import functools
import json
from logging import getLogger

from .compat import odict, ensure_text_type
from ..auxlib.entity import EntityEncoder

try:
    import ruamel.yaml as yaml
except ImportError:
    try:
        import ruamel_yaml as yaml
    except ImportError:
        raise ImportError("No yaml library available. To proceed, conda install ruamel.yaml")

log = getLogger(__name__)


def represent_ordereddict(dumper, data):
    value = []

    for item_key, item_value in data.items():
        node_key = dumper.represent_data(item_key)
        node_value = dumper.represent_data(item_value)

        value.append((node_key, node_value))

    return yaml.nodes.MappingNode(u'tag:yaml.org,2002:map', value)


yaml.representer.RoundTripRepresenter.add_representer(odict, represent_ordereddict)
yaml.representer.SafeRepresenter.add_representer(odict, represent_ordereddict)


# FUTURE: Python 3.9+, replace with functools.cache
@functools.lru_cache(maxsize=None)
def _yaml_round_trip():
    parser = yaml.YAML(typ="rt")
    parser.indent(mapping=2, offset=2, sequence=4)
    return parser


# FUTURE: Python 3.9+, replace with functools.cache
@functools.lru_cache(maxsize=None)
def _yaml_safe():
    parser = yaml.YAML(typ="safe", pure=True)
    parser.indent(mapping=2, offset=2, sequence=4)
    parser.default_flow_style = False
    return parser


def yaml_round_trip_load(string):
    return _yaml_round_trip().load(string)


def yaml_safe_load(string):
    """
    Examples:
        >>> yaml_safe_load("key: value")
        {'key': 'value'}

    """
    return _yaml_safe().load(string)


def yaml_round_trip_dump(object, stream=None):
    """dump object to string or stream"""
    ostream = stream or StringIO()
    _yaml_round_trip().dump(object, ostream)
    if not stream:
        return ostream.getvalue()


def yaml_safe_dump(object, stream=None):
    """dump object to string or stream"""
    ostream = stream or StringIO()
    _yaml_safe().dump(object, ostream)
    if not stream:
        return ostream.getvalue()


def json_load(string):
    return json.loads(string)


def json_dump(object):
    return ensure_text_type(json.dumps(object, indent=2, sort_keys=True,
                                       separators=(',', ': '), cls=EntityEncoder))
