# llmfiles/main.py
"""Main entry point for the llmfiles CLI application."""

import sys
# Attempt to set up paths for development if needed, or rely on installation
# from pathlib import Path
# SCRIPT_DIR = Path(__file__).resolve().parent
# sys.path.append(str(SCRIPT_DIR.parent)) # Add project root to path

# Import the main CLI group from the interface module
try:
    from llmfiles.cli.interface import main_cli_group
except ImportError as e:
    # This fallback might be needed if running main.py directly without full package installation
    # For a proper installed package, this shouldn't be necessary.
    # print(f"ImportError in main.py: {e}. Attempting to adjust sys.path for development.", file=sys.stderr)
    # from pathlib import Path
    # project_root = Path(__file__).resolve().parent.parent # Assuming main.py is in llmfiles/
    # sys.path.insert(0, str(project_root))
    # try:
    # from llmfiles.cli.interface import main_cli_group
    # except ImportError:
    print("Failed to import main_cli_group. Ensure llmfiles is installed correctly or paths are set up for development.", file=sys.stderr)
    raise


def entrypoint():
    """Function to be called by the script defined in pyproject.toml."""
    main_cli_group(prog_name="llmfiles")

if __name__ == '__main__':
    entrypoint()