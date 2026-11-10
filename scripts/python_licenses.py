"""Write or check the locked Python dependency licence inventory."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "python-licenses.json"
ALIASES = {
    "Apache License": "Apache-2.0",
    "Apache-2.0": "Apache-2.0",
    "BSD-3-Clause": "BSD-3-Clause",
    "MIT": "MIT",
    "MIT License": "MIT",
    "MPL-2.0": "MPL-2.0",
    "The BSD 2-Clause License": "BSD-2-Clause",
}
CLASSIFIERS = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}
ALLOWED_PARTS = {
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CNRI-Python",
    "MIT",
    "MIT-0",
    "PSF-2.0",
}
EXACT_EXCEPTIONS = {
    "certifi": "MPL-2.0",
    "tld": "MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later",
}


def expression(distribution: importlib.metadata.Distribution) -> str:
    metadata = distribution.metadata
    value = metadata.get("License-Expression") or metadata.get("License") or ""
    value = ALIASES.get(value, value)
    classifier_values = {
        CLASSIFIERS[classifier]
        for classifier in metadata.get_all("Classifier", [])
        if classifier in CLASSIFIERS
    }
    if len(classifier_values) > 1 and " OR " not in value.upper():
        value = " AND ".join(sorted(classifier_values, reverse=True))
    elif not value and classifier_values:
        value = next(iter(classifier_values))
    return re.sub(r"\s+", " ", value).strip()


def inventory() -> list[dict[str, str]]:
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("Install uv to read the locked dependencies.")
    exported = subprocess.check_output(  # noqa: S603  # Fixed command and arguments.
        [
            uv,
            "export",
            "--project",
            str(ROOT),
            "--frozen",
            "--no-dev",
            "--no-hashes",
            "--no-emit-project",
            "--no-annotate",
            "--no-header",
        ],
        text=True,
    )
    rows = []
    for line in exported.splitlines():
        if not line or line.startswith("-e ") or "sys_platform == 'win32'" in line:
            continue
        if "sys_platform == 'emscripten'" in line:
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)==([^ ;]+)", line)
        if not match:
            raise ValueError(f"Cannot read the dependency export line: {line}")
        name, expected_version = match.groups()
        distribution = importlib.metadata.distribution(name)
        if distribution.version != expected_version:
            raise ValueError(f"{name} is {distribution.version}, not locked {expected_version}")
        rows.append(
            {
                "name": distribution.metadata["Name"],
                "version": distribution.version,
                "license": expression(distribution),
            }
        )
    sdk = importlib.metadata.distribution("leashbyte-sdk")
    rows.append({"name": "leashbyte-sdk", "version": sdk.version, "license": expression(sdk)})
    return sorted(rows, key=lambda row: row["name"].lower())


def validate(rows: list[dict[str, str]]) -> None:
    for row in rows:
        value = row["license"]
        if not value or "UNKNOWN" in value.upper():
            raise ValueError(f"Unknown licence for {row['name']}")
        if re.search(r"AGPL|SSPL", value, re.IGNORECASE):
            raise ValueError(f"Forbidden licence for {row['name']}: {value}")
        expected = EXACT_EXCEPTIONS.get(row["name"].lower())
        if expected is not None:
            if value != expected:
                raise ValueError(f"Changed licence exception for {row['name']}: {value}")
            continue
        parts = set(re.findall(r"[A-Za-z][A-Za-z0-9.-]+", value))
        parts -= {"AND", "OR", "WITH", "License"}
        unknown = parts - ALLOWED_PARTS
        if unknown:
            raise ValueError(f"Unreviewed licence for {row['name']}: {sorted(unknown)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rows = inventory()
    validate(rows)
    rendered = json.dumps(rows, indent=2) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit(
                "Run python scripts/python_licenses.py and commit the changed inventory."
            )
    else:
        OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"Checked {len(rows)} locked Python packages. No unknown, AGPL or SSPL licence.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, importlib.metadata.PackageNotFoundError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
