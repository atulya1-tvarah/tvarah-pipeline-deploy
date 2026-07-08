from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from training_data_builder import build_training_exports


DEFAULT_EXTRACTOR_ROOT = Path(r"E:\Dev\profile-extraction-ml-poc\profile-extraction-ml-poc-deployment")


def _resolve_python(extractor_root: Path) -> str:
    candidate = extractor_root / ".venv" / "Scripts" / "python.exe"
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _run_extraction(extractor_root: Path, input_dir: Path, extracted_output_dir: Path) -> None:
    python_exe = _resolve_python(extractor_root)
    batch_script = extractor_root / "batch_resume.py"
    if not batch_script.exists():
        raise FileNotFoundError(f"Could not find batch_resume.py at {batch_script}")
    extracted_output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        python_exe,
        str(batch_script),
        "--input",
        str(input_dir),
        "--output",
        str(extracted_output_dir),
    ]
    subprocess.run(command, cwd=str(extractor_root), check=True)


def run_pipeline(
    extractor_root: str,
    input_dir: str,
    extracted_json_dir: str,
    training_output_dir: str,
    skip_extraction: bool = False,
) -> dict[str, object]:
    extractor_root_path = Path(extractor_root)
    input_dir_path = Path(input_dir)
    extracted_json_dir_path = Path(extracted_json_dir)
    training_output_dir_path = Path(training_output_dir)

    if not skip_extraction:
        _run_extraction(extractor_root_path, input_dir_path, extracted_json_dir_path)

    training_result = build_training_exports(
        [str(extracted_json_dir_path)],
        str(training_output_dir_path),
    )
    return {
        "extractor_root": str(extractor_root_path),
        "input_dir": str(input_dir_path),
        "extracted_json_dir": str(extracted_json_dir_path),
        "training_output_dir": str(training_output_dir_path),
        "skip_extraction": skip_extraction,
        "training_result": training_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run resume extraction from the profile-extraction-ml-poc project and convert the resulting JSON files into BERT/LLM training exports."
    )
    parser.add_argument(
        "--extractor-root",
        default=str(DEFAULT_EXTRACTOR_ROOT),
        help="Path to the profile-extraction-ml-poc deployment root.",
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Folder containing resume PDF or DOCX files.",
    )
    parser.add_argument(
        "--extracted-json-dir",
        required=True,
        help="Folder where extracted resume JSON files should be written.",
    )
    parser.add_argument(
        "--training-output-dir",
        required=True,
        help="Folder where the downstream training JSONL exports should be written.",
    )
    parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Skip the extractor run and build training exports from an existing extracted JSON folder.",
    )
    args = parser.parse_args()

    result = run_pipeline(
        extractor_root=args.extractor_root,
        input_dir=args.input_dir,
        extracted_json_dir=args.extracted_json_dir,
        training_output_dir=args.training_output_dir,
        skip_extraction=args.skip_extraction,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
