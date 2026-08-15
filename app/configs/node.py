import os

import yaml


def dfs_import(file_path):
    result = dict()
    _current_path = os.path.dirname(os.path.abspath(file_path))
    yaml_f = open(file_path, 'r', encoding='utf8')

    conf = yaml.safe_load(yaml_f)
    for include in conf.get('include', list()):
        sub_path = os.path.join(_current_path, include)
        result.update(dfs_import(sub_path))
    if 'include' in conf:
        conf.pop('include')

    result.update(conf)
    return result


def create_node_configs():
    return dfs_import(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'configs.yaml'))
