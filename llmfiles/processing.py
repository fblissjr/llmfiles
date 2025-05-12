# llmfiles/processing.py
"""file content processing, including conditional yaml transformations."""

import structlog  # use structlog
from pathlib import Path
from typing import Optional, Tuple, Any, Dict, List

from llmfiles.config import PromptConfig, SortMethod
from llmfiles.util import strip_utf8_bom, get_language_hint

log = structlog.get_logger(__name__)  # module-level logger

# --- optional pyyaml import for yaml processing ---
pyyaml_available = False
yaml_module: Any = None  # placeholder for the imported 'yaml' module
try:
    import yaml  # try to import the standard yaml library (pyyaml)

    yaml_module = yaml
    pyyaml_available = True
    log.debug("pyyaml_loaded_successfully", feature="yaml_processing")
except ImportError:
    log.debug(
        "pyyaml_not_found",
        feature="yaml_processing",
        note="yaml truncation will be skipped if requested.",
    )

# --- yaml truncation helper functions ---
def _truncate_yaml_fields_recursive(
    data_node: Any, placeholder: str, max_len: int
) -> Tuple[Any, bool]:
    """
    recursively truncates long string/bytes values at the targeted path
    `interactions[*].response.body.string` in parsed yaml data (python objects).
    returns (potentially_modified_node, was_modified_flag).
    """
    modified_here = False
    if isinstance(data_node, dict):
        # specific path targeting for truncation.
        if "interactions" in data_node and isinstance(data_node["interactions"], list):
            for interaction in data_node["interactions"]:
                if (
                    isinstance(interaction, dict)
                    and isinstance(interaction.get("response"), dict)
                    and isinstance(interaction["response"].get("body"), dict)
                    and "string" in interaction["response"]["body"]
                ):
                    content_field = interaction["response"]["body"]["string"]
                    should_truncate_field = False
                    original_len = 0

                    if isinstance(
                        content_field, bytes
                    ):  # content from a `!!binary` tag
                        original_len = len(content_field)
                        if original_len > max_len:
                            should_truncate_field = True
                    elif isinstance(
                        content_field, str
                    ):  # content from a plain string field
                        original_len = len(content_field)
                        if original_len > max_len:
                            should_truncate_field = True

                    if should_truncate_field:
                        interaction["response"]["body"]["string"] = placeholder
                        modified_here = True
                        log.debug(
                            "yaml_field_truncated",
                            path="response.body.string",
                            original_len=original_len,
                            max_len=max_len,
                        )

        # general recursion for other dictionary items.
        # this allows processing nested structures if the target path could be deeper,
        # or if other truncation rules were to be added.
        for key, value in data_node.items():
            # avoid re-processing 'interactions' list if specific handling is considered exhaustive.
            if key == "interactions" and isinstance(value, list):
                continue

            new_value, item_modified = _truncate_yaml_fields_recursive(
                value, placeholder, max_len
            )
            if item_modified:
                data_node[key] = new_value
                modified_here = True  # propagate modification flag upwards
        return data_node, modified_here

    elif isinstance(data_node, list):
        # if a list is encountered, process each item recursively.
        new_list_elements = list(
            data_node
        )  # operate on a copy for potential modification
        list_items_modified = False
        for i, item in enumerate(data_node):
            new_item, item_modified_flag = _truncate_yaml_fields_recursive(
                item, placeholder, max_len
            )
            if item_modified_flag:
                new_list_elements[i] = new_item
                list_items_modified = True
        # return the modified list only if changes were made.
        return (
            new_list_elements if list_items_modified else data_node,
            list_items_modified,
        )

    # if not a dict or list, return the node as is.
    return data_node, False


def _apply_yaml_field_truncation(
    yaml_content_str: str, config: PromptConfig
) -> Tuple[str, bool]:
    """
    parses a yaml string, applies field truncation, and returns the new yaml string.
    also returns a flag indicating if any modifications were made.
    """
    if not (
        pyyaml_available and yaml_module
    ):  # check if pyyaml was successfully imported
        log.warning(
            "yaml_truncation_skipped_no_pyyaml",
            reason="pyyaml library not available for processing.",
        )
        return yaml_content_str, False  # return original content, no modification

    try:
        # `safe_load_all` handles multi-document yaml files.
        parsed_yaml_documents = list(yaml_module.safe_load_all(yaml_content_str))
        if (
            not parsed_yaml_documents
        ):  # file was empty or contained no valid yaml documents
            log.debug(
                "yaml_truncation_skipped_empty_or_invalid",
                reason="no documents found in yaml string.",
            )
            return yaml_content_str, False

        file_content_was_modified = False
        documents_for_output = []
        for document_data_structure in parsed_yaml_documents:
            # apply recursive truncation to each loaded yaml document (python object).
            processed_document, doc_struct_was_modified = (
                _truncate_yaml_fields_recursive(
                    document_data_structure,
                    config.yaml_truncate_placeholder,
                    config.yaml_truncate_content_max_len,
                )
            )
            documents_for_output.append(processed_document)
            if doc_struct_was_modified:
                file_content_was_modified = True

        if file_content_was_modified:
            # serialize the (potentially modified) list of documents back to a yaml string.
            modified_yaml_output_str = yaml_module.dump_all(
                documents_for_output,
                default_flow_style=False,  # use block style for readability
                allow_unicode=True,
                sort_keys=False,  # preserve original key order where possible
                width=100000,  # effectively disable pyyaml's auto line wrapping
                indent=2,
            )
            log.info("yaml_content_modified_by_truncation")
            return (
                modified_yaml_output_str.strip(),
                True,
            )  # strip any trailing whitespace from dump_all

        log.debug(
            "yaml_truncation_no_changes_made",
            reason="no targeted fields met truncation criteria.",
        )
        return yaml_content_str, False  # no modifications were made

    except yaml_module.YAMLError as e:  # pyyaml specific parsing error
        log.warning(
            "yaml_parsing_error_during_truncation",
            error=str(e),
            note="original content will be used instead.",
        )
        return yaml_content_str, False
    except Exception as e:  # catch any other unexpected errors
        log.error("unexpected_error_in_yaml_truncation", error=str(e), exc_info=True)
        return yaml_content_str, False


# --- main file content processing function ---
def process_file_content(
    file_path: Path, config: PromptConfig
) -> Optional[Tuple[str, str, Optional[float]]]:
    """
    reads, processes (including optional yaml truncation), and formats file content.
    returns a tuple: (formatted_content_for_llm, raw_content_for_template, modification_time_unix).
    returns none if the file should be skipped.
    """
    log.debug("processing_file_content", path=str(file_path))
    try:
        raw_bytes_from_file = file_path.read_bytes()
    except (
        Exception
    ) as e:  # handles filenotfound, permissionerror, oserror, memoryerror
        log.warning("error_reading_file_bytes", path=str(file_path), error=str(e))
        return None

    try:
        # 1. initial decode from bytes to string (utf-8, bom stripped).
        #    `errors='replace'` inserts U+FFFD for undecodable byte sequences.
        current_text_content = strip_utf8_bom(raw_bytes_from_file).decode(
            "utf-8", errors="replace"
        )
        # `raw_content_for_template` stores content *after* potential yaml processing but *before* llm formatting.
        raw_content_for_template = current_text_content

        # 2. yaml-specific processing (if applicable and enabled).
        #    this step modifies `current_text_content` and `raw_content_for_template` if changes occur.
        is_yaml_file_type = file_path.suffix.lower() in [".yaml", ".yml"]
        was_yaml_content_modified = False
        if is_yaml_file_type and config.process_yaml_truncate_long_fields:
            log.debug("applying_yaml_truncation", path=str(file_path))
            current_text_content, was_yaml_content_modified = (
                _apply_yaml_field_truncation(current_text_content, config)
            )
            if was_yaml_content_modified:
                raw_content_for_template = (
                    current_text_content  # update raw with truncated version
                )
                log.info("yaml_content_truncated", path=str(file_path))

        # 3. skip if decoding errors persist (likely binary) or if content is empty.
        #    this check is after yaml processing, as truncation might make a file "valid text".
        if "\ufffd" in current_text_content:
            # if it's yaml, truncation was on, it *was* modified, but still has \ufffd, it's problematic.
            if (
                is_yaml_file_type
                and config.process_yaml_truncate_long_fields
                and was_yaml_content_modified
            ):
                log.info(
                    "skipping_yaml_with_persistent_decode_errors_after_truncation",
                    path=str(file_path),
                )
                return None
            elif (
                not is_yaml_file_type
            ):  # if not yaml and has decode errors, it's likely binary.
                log.info("skipping_file_decode_errors_non_yaml", path=str(file_path))
                return None
            # if yaml, truncation off/failed, and has errors, it's skipped by this.

        if not current_text_content.strip():  # skip empty or all-whitespace files.
            log.info("skipping_empty_file", path=str(file_path))
            return None

        # 4. apply line numbering and markdown code block formatting for llm prompt.
        content_lines = current_text_content.splitlines()
        if config.line_numbers:
            max_line_num_width = len(str(len(content_lines)))
            content_lines = [
                f"{str(i + 1):>{max_line_num_width}} | {line}"
                for i, line in enumerate(content_lines)
            ]

        llm_formatted_content = "\n".join(content_lines)
        if not config.no_codeblock:
            lang_hint = get_language_hint(
                file_path.suffix[1:] if file_path.suffix else ""
            )
            # ensure backtick wrapper doesn't clash with content.
            backtick_seq = "```"
            while backtick_seq in llm_formatted_content:
                backtick_seq += "`"
            llm_formatted_content = (
                f"{backtick_seq}{lang_hint}\n{llm_formatted_content}\n{backtick_seq}"
            )

        # 5. get file modification time if needed for sorting.
        file_mod_time: Optional[float] = None
        if config.sort_method in [SortMethod.DATE_ASC, SortMethod.DATE_DESC]:
            try:
                file_mod_time = file_path.stat().st_mtime
            except OSError as e:
                log.warning("could_not_get_mod_time", path=str(file_path), error=str(e))

        log.debug("file_content_processed_successfully", path=str(file_path))
        return llm_formatted_content, raw_content_for_template, file_mod_time

    except Exception as e:  # catch-all for unexpected errors during processing stages.
        log.error(
            "unexpected_error_processing_file_content",
            path=str(file_path),
            error=str(e),
            exc_info=True,
        )
        return None