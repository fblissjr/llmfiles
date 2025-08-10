from llmfiles.cli.interface import main_cli_group

def entrypoint():
    # main entry point for the script defined in pyproject.toml.
    main_cli_group(prog_name="llmfiles")

if __name__ == '__main__':
    entrypoint()
