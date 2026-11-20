"""Research deployment only; not shipped in the robot application."""
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path
archive, destination, expected = sys.argv[1:]
archive, destination = Path(archive), Path(destination)
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    raise RuntimeError('Archive digest mismatch')
destination.mkdir()  # Refuse to overwrite an existing run.
with tarfile.open(archive) as package:
    for member in package.getmembers():
        if not (destination / member.name).resolve().is_relative_to(destination.resolve()) or member.issym() or member.islnk():
            raise RuntimeError('Archive leaves the run directory')
    package.extractall(destination)
job = subprocess.check_output(['sbatch', '--dependency=afterany:20780', '--parsable', str(destination / 'run.sbatch')], text=True).strip()
result = dict(job_id=job, archive_sha256=expected, destination=str(destination))
(destination / 'admission.json').write_text(json.dumps(result))
print(json.dumps(result))
