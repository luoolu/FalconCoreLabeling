import os.path as osp

try:
    import importlib.resources as pkg_resources
except ImportError:
    # Try backported to PY<37 `importlib_resources`.
    import importlib_resources as pkg_resources

import yaml

from anylabeling import configs as anylabeling_configs

from .views.labeling.logger import logger


# Save current config file
current_config_file = None


def update_dict(target_dict, new_dict, validate_item=None):
    for key, value in new_dict.items():
        if validate_item:
            validate_item(key, value)
        # Special handling for recognized new keys
        if key not in target_dict and key in ["theme", "ui"]:
            target_dict[key] = value
            continue
        if key not in target_dict:
            logger.warning("Skipping unexpected key in config: %s", key)
            continue
        if isinstance(target_dict[key], dict) and isinstance(value, dict):
            update_dict(target_dict[key], value, validate_item=validate_item)
        else:
            target_dict[key] = value


def save_config(config):
    # Local config file
    user_config_file = osp.join(osp.expanduser("~"), ".anylabelingrc")
    try:
        with open(user_config_file, "w") as f:
            yaml.safe_dump(config, f)
    except Exception:  # noqa
        logger.warning("Failed to save config: %s", user_config_file)


def get_default_config():
    config_file = "anylabeling_config.yaml"
    with pkg_resources.open_text(anylabeling_configs, config_file) as f:
        config = yaml.safe_load(f)

    # Save default config to ~/.anylabelingrc
    if not osp.exists(osp.join(osp.expanduser("~"), ".anylabelingrc")):
        save_config(config)

    return config


def validate_config_item(key, value):
    if key == "validate_label" and value not in [None, "exact"]:
        raise ValueError(f"Unexpected value for config key 'validate_label': {value}")
    if key == "shape_color" and value not in [None, "auto", "manual"]:
        raise ValueError(f"Unexpected value for config key 'shape_color': {value}")
    if key == "labels" and value is not None and len(value) != len(set(value)):
        raise ValueError(f"Duplicates are detected for config key 'labels': {value}")
    if key == "label_sets" and value is not None and not isinstance(value, dict):
        raise ValueError("label_sets must be a dictionary")
    if key == "theme" and value not in ["system", "light", "dark"]:
        raise ValueError(f"Unexpected value for config key 'theme': {value}")
    if key == "line_width" and (not isinstance(value, int) or value <= 0):
        raise ValueError("line_width must be a positive integer")
    if key == "fill_opacity" and (
            not isinstance(value, int) or not 0 <= value <= 255
    ):
        raise ValueError("fill_opacity must be an integer from 0 to 255")


def get_config(config_file_or_yaml=None, config_from_args=None):
    # 1. default config
    config = get_default_config()

    # 2. specified as file or yaml
    if config_file_or_yaml is None:
        config_file_or_yaml = current_config_file
    if config_file_or_yaml is not None:
        config_from_yaml = yaml.safe_load(config_file_or_yaml)
        if not isinstance(config_from_yaml, dict):
            with open(config_from_yaml) as f:
                logger.info("Loading config file from: %s", config_from_yaml)
                config_from_yaml = yaml.safe_load(f)
        update_dict(config, config_from_yaml, validate_item=validate_config_item)

    # 3. command line argument or specified config file
    if config_from_args is not None:
        update_dict(config, config_from_args, validate_item=validate_config_item)

    return config
