"""Soak automatisé du mode toggle : 50 cycles start/stop sans voix réelle.

Valide le critère technique de l'étape 1 (§19) : 50 enregistrements
successifs sans fuite ni overflow. Ne valide PAS la qualité du contenu
audio (pas de voix humaine ici) — ça reste à faire en live par
l'utilisateur en mode hold.
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N_TAKES = 50
TAKE_MS = 150


def main() -> int:
    proc = subprocess.Popen(
        [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "main.py"),
         "--ptt-mode", "toggle", "--out-dir", str(ROOT / "recordings" / "soak_toggle")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=ROOT,
    )
    assert proc.stdin is not None

    for _ in range(N_TAKES):
        proc.stdin.write("\n")
        proc.stdin.flush()
        time.sleep(TAKE_MS / 1000)
        proc.stdin.write("\n")
        proc.stdin.flush()
    proc.stdin.write("q\n")
    proc.stdin.flush()

    out, _ = proc.communicate(timeout=30)
    print(out)
    print("exit code:", proc.returncode)

    takes = sorted((ROOT / "recordings" / "soak_toggle").glob("take_*.wav"))
    print(f"WAV écrits: {len(takes)}")
    last_line = out.strip().splitlines()[-1] if out.strip() else ""
    final_overflow_ok = "overflow=0, underflow=0" in last_line
    print("Dernière ligne:", last_line or "<vide>")
    return 0 if (proc.returncode == 0 and len(takes) == N_TAKES and final_overflow_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
