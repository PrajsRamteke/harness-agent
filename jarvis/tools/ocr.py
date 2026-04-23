"""OCR tool: read_image_text — extracts text from any image using macOS Vision framework."""
import subprocess
import os
import pathlib

from ..constants import CWD


def read_image_text(path: str) -> str:
    """Extract all text from an image file using macOS Vision framework (on-device OCR)."""
    # Resolve path
    p = (CWD / path).resolve() if not os.path.isabs(path) else pathlib.Path(path)
    if not p.exists():
        return f"ERROR: {path} not found"
    if not p.is_file():
        return f"ERROR: {path} is not a file"

    swift_code = f'''
import Vision
import Foundation

let url = URL(fileURLWithPath: "{p}")
let request = VNRecognizeTextRequest {{ req, err in
    guard let obs = req.results as? [VNRecognizedTextObservation] else {{ return }}
    for o in obs {{
        if let top = o.topCandidates(1).first {{
            print(top.string)
        }}
    }}
}}
request.recognitionLevel = .accurate
let handler = VNImageRequestHandler(url: url, options: [:])
try? handler.perform([request])
'''

    result = subprocess.run(
        ["swift", "-e", swift_code],
        capture_output=True,
        text=True,
        timeout=30,
    )

    output = result.stdout.strip()
    if not output:
        stderr = result.stderr.strip()
        return f"ERROR: No text found in image. stderr: {stderr}" if stderr else "No text detected in image."
    return output
