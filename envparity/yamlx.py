"""YAML loading that remembers line numbers of mapping keys and list items."""
from __future__ import annotations

import yaml


class LDict(dict):
    line = 0
    lines: dict


class LList(list):
    line = 0
    lines: list


class _Loader(yaml.SafeLoader):
    pass


def _mapping(loader: _Loader, node: yaml.MappingNode) -> LDict:
    loader.flatten_mapping(node)
    result = LDict()
    result.line = node.start_mark.line + 1
    result.lines = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        value = loader.construct_object(value_node, deep=True)
        try:
            result[key] = value
        except TypeError:
            continue
        result.lines[key] = key_node.start_mark.line + 1
    return result


def _sequence(loader: _Loader, node: yaml.SequenceNode) -> LList:
    result = LList(loader.construct_sequence(node, deep=True))
    result.line = node.start_mark.line + 1
    result.lines = [item.start_mark.line + 1 for item in node.value]
    return result


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_SEQUENCE_TAG, _sequence)


def load_all(text: str) -> list:
    return [doc for doc in yaml.load_all(text, Loader=_Loader) if doc is not None]


def line_of(container, key, default: int = 1) -> int:
    lines = getattr(container, "lines", None)
    if isinstance(container, dict) and isinstance(lines, dict):
        return lines.get(key, getattr(container, "line", default))
    if isinstance(container, list) and isinstance(lines, list) and isinstance(key, int) and key < len(lines):
        return lines[key]
    return getattr(container, "line", default)
