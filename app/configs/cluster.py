import yaml
import os


def create_cluster_configs():
    return yaml.safe_load(
        open(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'cluster.configs.yaml'), encoding='utf-8'
        )
    )