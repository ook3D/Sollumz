"""AMD Compressonator backend, converting textures with `compressonatorcli`.

The preset arguments are passed to `compressonatorcli` as they are. Jobs with the same arguments are converted in
a single run of its folder mode (`compressonatorcli <args> in/ out/`), which is much faster than one process per
texture. A folder run stops at the first file it cannot read, so that file is reported as failed and the run is
repeated for the textures it did not get to.

`compressonatorcli` cannot open non-ASCII paths, so it works on ASCII-named copies of the inputs, in an ASCII
working directory. If there is none, each texture is converted in its own run, with relative file names.
"""

import os
import re
import shlex
import shutil
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path

from ... import logger
from ..presets import PresetCategory, register_preset_category
from . import CompressJob, CompressorBackend, register_backend

DEFAULT_EXECUTABLE_PATH = r"C:\Compressonator_4.5.52\bin\CLI\compressonatorcli.exe"

_CONVERSION_TIMEOUT_SECONDS = 600

# Printed by compressonatorcli before converting each file of a folder run.
_PROCESSING_SOURCE_RE = re.compile(r"^Processing source\s*:\s*(.+?)\s*$", re.MULTILINE)


class _ConversionError(Exception):
    pass


def _run_cli(executable: Path, args: list[str], cwd: Path):
    try:
        result = subprocess.run(
            [str(executable), *args, "-noprogress"],
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",  # never raise UnicodeDecodeError on non-ASCII compressor output
            timeout=_CONVERSION_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        raise _ConversionError(f"failed to run compressonatorcli: {e}") from None
    except subprocess.TimeoutExpired:
        raise _ConversionError(f"timed out after {_CONVERSION_TIMEOUT_SECONDS}s") from None
    return result


def _cli_error(result) -> str:
    lines = [line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines() if line.strip()]
    errors = [line for line in lines if "error" in line.lower()]
    return "\n".join(errors[-3:] or lines[-3:]) or f"exit code {result.returncode}"


def _input_name(index: int, job: CompressJob) -> str:
    # compressonatorcli cannot open non-ASCII paths, so it works on ASCII-named copies of the inputs. It picks the
    # image format by extension, so the extension is kept.
    suffix = job.input.suffix.lower()
    return f"{index}{suffix if suffix.isascii() else ''}"


def _ascii_dir(path: Path) -> Path | None:
    """`path`, or its Windows 8.3 short form, if it is ASCII; else `None`. Folder mode turns the folders into
    absolute paths, so it needs an ASCII working directory (e.g. not a temp directory under a non-ASCII user name).
    """
    if str(path).isascii():
        return path
    if os.name == "nt":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer)) and buffer.value.isascii():
            return Path(buffer.value)  # 8.3 names can be disabled per volume
    return None


def _convert_file(executable: Path, args: list[str], job: CompressJob) -> str | None:
    """Converts one job in its own run, with relative file names, which work in any working directory. Returns an
    error message, or `None` on success."""
    work_dir = job.output.parent
    stem = f"_cmp_{uuid.uuid4().hex[:12]}"
    src, out = stem + _input_name(0, job), f"{stem}.dds"
    try:
        try:
            os.link(job.input, work_dir / src)
        except OSError:
            shutil.copyfile(job.input, work_dir / src)
        result = _run_cli(executable, [*args, src, out], work_dir)
        # compressonatorcli does not always set an error exit code, so a missing output is also a failure.
        if result.returncode != 0 or not (work_dir / out).is_file():
            return _cli_error(result)
        os.replace(work_dir / out, job.output)
        return None
    except (_ConversionError, OSError) as e:
        return str(e)
    finally:
        for name in (src, out):
            try:
                (work_dir / name).unlink(missing_ok=True)
            except OSError:
                pass


def _convert_group(executable: Path, args: list[str], jobs: Sequence[CompressJob], work_dir: Path) -> dict[int, str]:
    """Converts `jobs` (all with the same `args`) in folder mode runs. Returns the errors by job index."""
    errors: dict[int, str] = {}
    batch_dir = work_dir / f"_cmp_{uuid.uuid4().hex[:12]}"
    in_dir, out_dir = batch_dir / "in", batch_dir / "out"
    try:
        in_dir.mkdir(parents=True)
        out_dir.mkdir()
        pending: dict[str, int] = {}  # input file name -> job index
        for i, job in enumerate(jobs):
            name = _input_name(i, job)
            try:
                try:
                    os.link(job.input, in_dir / name)
                except OSError:
                    shutil.copyfile(job.input, in_dir / name)
            except OSError as e:
                errors[i] = str(e)
                continue
            pending[name] = i

        while pending:
            try:
                result = _run_cli(executable, [*args, "in/", "out/"], batch_dir)
            except _ConversionError as e:
                errors.update((i, str(e)) for i in pending.values())
                break

            for name, i in list(pending.items()):
                out = out_dir / f"{Path(name).stem}.dds"
                if out.is_file():
                    os.replace(out, jobs[i].output)
                    (in_dir / name).unlink()
                    del pending[name]
            if not pending:
                break

            # The run stopped at a file it could not read: the last one it started. Retry without it.
            started = _PROCESSING_SOURCE_RE.findall(result.stdout)
            failed = Path(started[-1]).name if started else None
            if failed not in pending:
                # Nothing to retry without, report everything left with the run's error.
                errors.update((i, _cli_error(result)) for i in pending.values())
                break
            errors[pending.pop(failed)] = _cli_error(result)
            (in_dir / failed).unlink()
    except OSError as e:
        errors.update((i, str(e)) for i in range(len(jobs)) if i not in errors and not jobs[i].output.is_file())
    finally:
        shutil.rmtree(batch_dir, ignore_errors=True)
    return errors


def run_jobs(executable: Path, jobs: Sequence[CompressJob]):
    """Converts all `jobs` to DDS, with one `compressonatorcli` folder mode run per distinct arguments.

    Never raises on conversion problems: a failed job is logged as a warning and leaves its output file missing,
    which the caller treats as a per-texture conversion failure.
    """
    if not jobs:
        return

    # The temporary files live alongside the outputs (all in the export's temp directory).
    work_dir = _ascii_dir(jobs[0].output.parent)
    errors: dict[int, str] = {}
    groups: dict[tuple[str, ...], list[int]] = {}
    for i, job in enumerate(jobs):
        try:
            groups.setdefault(tuple(shlex.split(job.args)), []).append(i)
        except ValueError as e:
            errors[i] = f"invalid arguments '{job.args}': {e}"

    for args, indices in groups.items():
        if work_dir is None:
            # No ASCII working directory for folder mode: one run per texture instead.
            for i in indices:
                if (error := _convert_file(executable, list(args), jobs[i])) is not None:
                    errors[i] = error
            continue
        group_errors = _convert_group(executable, list(args), [jobs[i] for i in indices], work_dir)
        errors.update((indices[k], error) for k, error in group_errors.items())

    for i, error in sorted(errors.items()):
        jobs[i].output.unlink(missing_ok=True)
        logger.warning(f"Compressonator failed to convert '{jobs[i].input.name}':\n{error}")


# Game id -> category. The bundled defaults of each game are in `compressonator_presets/<game>.json`.
COMPRESSONATOR_PRESET_CATEGORIES = {
    game: PresetCategory(
        id="texture_compressor_compressonator",
        label="Texture Compression (Compressonator)",
        game=game,
        bundled_defaults_path=Path(__file__).parent / "compressonator_presets" / f"{game}.json",
    )
    for game in ("gta5", "rdr2")
}
for _category in COMPRESSONATOR_PRESET_CATEGORIES.values():
    register_preset_category(_category)

COMPRESSONATOR_BACKEND = CompressorBackend(
    id="COMPRESSONATOR",
    label="AMD Compressonator",
    description="Convert textures with compressonatorcli, one run per distinct preset arguments",
    preset_categories=COMPRESSONATOR_PRESET_CATEGORIES,
    install_hint=(
        "Install AMD Compressonator, which includes compressonatorcli.",
        "If you install it elsewhere or another version, set the path above.",
    ),
    download_url="https://github.com/GPUOpen-Tools/compressonator/releases",
    executable_prop="compressonator_executable_path",
    run=run_jobs,
)
register_backend(COMPRESSONATOR_BACKEND)
