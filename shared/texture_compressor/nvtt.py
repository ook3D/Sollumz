"""NVIDIA Texture Tools backend, converting textures with ``nvtt_export.exe`` in batch mode."""

import subprocess
from collections.abc import Sequence
from pathlib import Path

from ... import logger
from ..presets import PresetCategory, register_preset_category
from . import CompressJob, CompressorBackend, register_backend

BATCH_FILE_NAME = "nvtt_batch.txt"

DEFAULT_EXECUTABLE_PATH = r"C:\Program Files\NVIDIA Corporation\NVIDIA Texture Tools\nvtt_export.exe"

_CONVERSION_TIMEOUT_SECONDS = 600

def _build_batch_line(job: CompressJob) -> str:
    # nvtt_export.exe's batch parser interprets backslashes as escape sequences (e.g. "\Users"
    # fails as an invalid unicode escape), so paths MUST use forward slashes. Quotes handle spaces.
    return f'"{job.input.as_posix()}" {job.args} --output "{job.output.as_posix()}"'


def run_batch(executable: Path, jobs: Sequence[CompressJob]):
    """Converts all `jobs` to DDS in a single `nvtt_export.exe --batch` invocation.

    Never raises on conversion problems: a missing executable, a failed/timed-out run, or an I/O
    error writing the batch file is logged as a warning and leaves the output files missing, which
    the caller treats as a per-texture conversion failure.
    """
    if not jobs:
        return

    # The batch file lives alongside the outputs (all in the export's temp directory), so it is
    # cleaned up together with them.
    batch_path = jobs[0].output.parent / BATCH_FILE_NAME
    try:
        batch_path.write_text("\n".join(_build_batch_line(job) for job in jobs), encoding="utf-8")
        # nvtt_export.exe cannot open a batch file whose command-line path is non-ASCII (e.g. a temp
        # dir under a non-ASCII user name), so run from the batch file's directory and pass its
        # ASCII name. Non-ASCII paths inside the UTF-8 batch file are read correctly.
        result = subprocess.run(
            [str(executable), "--batch", BATCH_FILE_NAME],
            cwd=batch_path.parent,
            capture_output=True,
            text=True,
            errors="replace",  # never raise UnicodeDecodeError on non-ASCII compressor output
            timeout=_CONVERSION_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        logger.warning(f"Texture compression failed to run nvtt_export.exe: {e}")
        return
    except subprocess.TimeoutExpired:
        logger.warning(
            f"Texture compression timed out after {_CONVERSION_TIMEOUT_SECONDS}s. No textures were converted."
        )
        _delete_outputs(jobs)
        return

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        logger.warning(
            f"nvtt_export.exe returned an error (exit code {result.returncode}). No textures were converted.\n{details}"
        )
        _delete_outputs(jobs)


def _delete_outputs(jobs: Sequence[CompressJob]):
    # After a killed or failed run there is no way to tell which outputs were fully written, and a
    # truncated DDS still has a valid header. Discard them all so none gets embedded.
    for job in jobs:
        try:
            job.output.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"Could not delete incomplete compressor output '{job.output}': {e}")


# Game id -> category. The bundled defaults of each game are in `nvtt_presets/<game>.json`.
NVTT_PRESET_CATEGORIES = {
    game: PresetCategory(
        id="texture_compressor_nvtt",
        label="Texture Compression (NVTT)",
        game=game,
        bundled_defaults_path=Path(__file__).parent / "nvtt_presets" / f"{game}.json",
    )
    for game in ("gta5", "rdr2")
}
for _category in NVTT_PRESET_CATEGORIES.values():
    register_preset_category(_category)

NVTT_BACKEND = CompressorBackend(
    id="NVTT",
    label="NVIDIA Texture Tools",
    description="Convert textures with nvtt_export.exe, launched once per export in batch mode",
    preset_categories=NVTT_PRESET_CATEGORIES,
    install_hint=(
        "Install the standalone NVIDIA Texture Tools Exporter (not the Photoshop plugin).",
        "It installs nvtt_export.exe at the default path. If you install it elsewhere, set the path above.",
    ),
    download_url="https://developer.nvidia.com/texture-tools-exporter",
    executable_prop="nvtt_executable_path",
    run=run_batch,
)
register_backend(NVTT_BACKEND)
