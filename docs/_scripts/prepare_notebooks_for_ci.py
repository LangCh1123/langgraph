"""Preprocess notebooks for CI. Currently removes pip install cells."""

import os
import json
import logging

logger = logging.getLogger(__name__)
NOTEBOOK_DIRS = ("docs/docs/how-tos",)

def remove_install_cells(notebook_path: str) -> None:
    with open(notebook_path, "r") as file:
        notebook = json.load(file)

    indices_to_delete = []
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] == "code":
            if any("pip install" in line for line in cell["source"]):
                indices_to_delete.append(index)

    for index in reversed(indices_to_delete):
        notebook["cells"].pop(index)

    with open(notebook_path, "w") as file:
        json.dump(notebook, file, indent=2)


def process_notebooks() -> None:
    for directory in NOTEBOOK_DIRS:
        for root, _, files in os.walk(directory):
            for file in files:
                if not file.endswith(".ipynb"):
                    continue

                notebook_path = os.path.join(root, file)
                try:
                    remove_install_cells(notebook_path)
                    logger.info(f"Processed: {notebook_path}")
                except Exception as e:
                    logger.error(f"Error processing {notebook_path}: {e}")


if __name__ == "__main__":
    process_notebooks()
    logger.info("All notebooks processed successfully.")
