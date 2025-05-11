# llmfiles/processing.py
"""File content processing, including conditional YAML transformations."""

import logging
from pathlib import Path
from typing import Optional, Tuple, Any, Dict, List

from .config import PromptConfig, SortMethod
from .util import strip_utf8_bom, get_language_hint

logger = logging.getLogger(__name__)

PYYAML_AVAILABLE = False
yaml_module: Any = None
try:
    import yaml

    yaml_module = yaml  # Assign to module-level var for use in functions
    PYYAML_AVAILABLE = True
    logger.debug("PyYAML library loaded successfully for YAML processing.")
except ImportError:
    logger.debug(
        "PyYAML not found. YAML-specific processing (e.g., truncation) will be skipped if requested."
    )


def _truncate_targeted_yaml_fields(
    data_node: Any, placeholder: str, max_len: int
) -> Tuple[Any, bool]:
    """
    Recursively truncates long string/bytes values at `interactions[*].response.body.string`
    in parsed YAML data.
    """
    modified = False
    if isinstance(data_node, dict):
        if "interactions" in data_node and isinstance(data_node["interactions"], list):
            for interaction in data_node["interactions"]:
                if (
                    isinstance(interaction, dict)
                    and isinstance(interaction.get("response"), dict)
                    and isinstance(interaction["response"].get("body"), dict)
                    and "string" in interaction["response"]["body"]
                ):
                    content = interaction["response"]["body"]["string"]
                    should_truncate = False
                    content_len = 0
                    if isinstance(content, bytes):  # From !!binary
                        content_len = len(content)
                        if content_len > max_len:
                            should_truncate = True
                    elif isinstance(content, str):  # Plain string
                        content_len = len(content)
                        if content_len > max_len:
                            should_truncate = True

                    if should_truncate:
                        interaction["response"]["body"]["string"] = placeholder
                        modified = True
                        logger.debug(
                            f"Truncated YAML field (len: {content_len}) at response.body.string."
                        )

        # Recurse for other dictionary values
        for key, value in data_node.items():
            # Avoid re-processing 'interactions' list items if specific handling is exhaustive
            if key == "interactions" and isinstance(value, list):
                continue

            new_value, item_modified = _truncate_targeted_yaml_fields(
                value, placeholder, max_len
            )
            if item_modified:
                data_node[key] = new_value
                modified = True
        return data_node, modified

    elif isinstance(data_node, list):
        new_list = list(data_node)  # Create a mutable copy
        list_modified_locally = False
        for i, item in enumerate(data_node):
            new_item, item_modified = _truncate_targeted_yaml_fields(
                item, placeholder, max_len
            )
            if item_modified:
                new_list[i] = new_item
                list_modified_locally = True
        return new_list if list_modified_locally else data_node, list_modified_locally

    return data_node, False


def _apply_yaml_truncation(yaml_str: str, config: PromptConfig) -> Tuple[str, bool]:
    """Parses YAML string, applies truncation, returns new YAML string and modification flag."""
    if not (PYYAML_AVAILABLE and yaml_module):  # Ensure module is loaded
        logger.warning("YAML truncation requested but PyYAML not available. Skipping.")
        return yaml_str, False

    try:
        documents = list(yaml_module.safe_load_all(yaml_str))
        if not documents:
            return yaml_str, False  # Empty or non-YAML

        overall_modified = False
        processed_docs = []
        for doc in documents:
            processed_doc, doc_modified = _truncate_targeted_yaml_fields(
                doc,
                config.yaml_truncate_placeholder,
                config.yaml_truncate_content_max_len,
            )
            processed_docs.append(processed_doc)
            if doc_modified:
                overall_modified = True

        if overall_modified:
            new_yaml_str = yaml_module.dump_all(
                processed_docs,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=100000,
                indent=2,
            )
            return new_yaml_str.strip(), True
        return yaml_str, False

    except yaml_module.YAMLError as e:
        logger.warning(
            f"YAML parsing error during truncation: {e}. Using original content."
        )
        return yaml_str, False
    except Exception as e:
        logger.error(f"Unexpected error in YAML truncation: {e}", exc_info=True)
        return yaml_str, False


def process_file_content(
    file_path: Path, config: PromptConfig
) -> Optional[Tuple[str, str, Optional[float]]]:
    """Reads, processes (optionally truncating YAML), and formats file content."""
    logger.debug(f"Processing file: {file_path}")
    try:
        raw_bytes = file_path.read_bytes()
    except Exception as e:  # Catch all read errors (Permission, NotFound, Memory, OS)
        logger.warning(f"Error reading file {file_path}: {e}. Skipping.")
        return None

    try:
        content_str = strip_utf8_bom(raw_bytes).decode("utf-8", errors="replace")
        raw_content_for_template = content_str  # This may be updated by YAML processing

        is_yaml_file = file_path.suffix.lower() in [".yaml", ".yml"]
        yaml_modified = False
        if is_yaml_file and config.process_yaml_truncate_long_fields:
            logger.debug(f"Applying YAML truncation for: {file_path}")
            content_str, yaml_modified = _apply_yaml_truncation(content_str, config)
            if yaml_modified:
                raw_content_for_template = (
                    content_str  # Use modified content as "raw" for template
                )
                logger.info(f"YAML content truncated in: {file_path}")

        # Skip if decoding errors remain (U+FFFD) and it wasn't a successfully processed YAML
        if "\ufffd" in content_str and not (
            is_yaml_file and yaml_modified and "\ufffd" not in content_str
        ):
            logger.info(
                f"Skipping '{file_path}' due to decoding errors (likely binary)."
            )
            return None
        if not content_str.strip():  # Skip empty or whitespace-only files
            logger.info(f"Skipping '{file_path}' as it's empty or whitespace-only.")
            return None

        # Line numbering and code block formatting
        lines = content_str.splitlines()
        if config.line_numbers:
            max_w = len(str(len(lines)))
            lines = [f"{str(i + 1):>{max_w}} | {line}" for i, line in enumerate(lines)]

        formatted_content = "\n".join(lines)
        if not config.no_codeblock:
            hint = get_language_hint(file_path.suffix[1:] if file_path.suffix else "")
            ticks = "```"
            while ticks in formatted_content:
                ticks += "`"
            formatted_content = f"{ticks}{hint}\n{formatted_content}\n{ticks}"

        mod_time = None
        if config.sort_method in [SortMethod.DATE_ASC, SortMethod.DATE_DESC]:
            try:
                mod_time = file_path.stat().st_mtime
            except OSError as e:
                logger.warning(f"Could not get mod_time for {file_path}: {e}")

        return formatted_content, raw_content_for_template, mod_time
    except Exception as e:
        logger.error(
            f"Unexpected error processing content for {file_path}: {e}", exc_info=True
        )
        return None