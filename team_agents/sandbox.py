"""Run an agent's shell command in the macOS sandbox: it can read and write
only its own workspace and the run's shared one, reach the internet but not
this machine's local services, and sees none of the operator's credentials."""

import os
from functools import lru_cache
from pathlib import Path
import subprocess

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_DEVELOPER_DIRS = ("/Applications/Xcode.app/Contents/Developer", "/Library/Developer/CommandLineTools")
# Runtime files a command needs; nothing under /Users.
_READABLE = (
    "/usr", "/bin", "/sbin", "/System", "/Library/Developer", "/Library/Frameworks",
    "/Applications/Xcode.app", "/opt/homebrew/bin", "/opt/homebrew/opt", "/opt/homebrew/Cellar",
    "/opt/homebrew/lib", "/opt/homebrew/share", "/opt/homebrew/etc/openssl@3",
    "/opt/homebrew/etc/ca-certificates", "/private/var/db", "/private/var/select",
    "/private/etc", "/dev",
)
# DNS and certificate checks. No keychain, launch, or Apple events services.
_SERVICES = (
    "com.apple.dnssd.service", "com.apple.SystemConfiguration.DNSConfiguration",
    "com.apple.trustd", "com.apple.trustd.agent", "com.apple.TrustEvaluationAgent",
)
PROFILE = "\n".join((
    "(version 1)",
    "(deny default)",
    "(allow process-exec process-fork)",
    "(allow signal (target same-sandbox))",
    "(allow sysctl-read)",
    "(allow file-read-metadata)",
    '(allow file-read* (literal "/") (literal "/Library/Preferences/com.apple.dt.Xcode.plist") '
    + " ".join(f'(subpath "{path}")' for path in _READABLE) + ")",
    '(allow file-write-data (literal "/dev/null"))',
    '(allow file-read* file-write* (subpath (param "OWN")) (subpath (param "SHARED")))',
    # The workspaces themselves can't be removed or replaced with a link.
    '(deny file-write-create file-write-unlink (literal (param "OWN")) (literal (param "SHARED")))',
    "(allow system-socket)",
    # Internet only: no local sockets (Docker, agents, daemons) beyond name lookup.
    "(allow network-outbound (remote ip))",
    '(allow network-outbound (literal "/private/var/run/mDNSResponder"))',
    '(deny network-outbound (remote ip "localhost:*"))',
    "(allow mach-lookup " + " ".join(f'(global-name "{name}")' for name in _SERVICES) + ")",
))


def _wrap(argv, own, shared, profile=PROFILE):
    return [SANDBOX_EXEC, "-D", f"OWN={own}", "-D", f"SHARED={shared}", "-p", profile, *argv]


@lru_cache(maxsize=1)
def available():
    """False off macOS, or where a sandbox can't be started (e.g. inside one)."""
    try:
        return subprocess.run(_wrap(["/usr/bin/true"], "/private/tmp", "/private/tmp"),
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run(command, own, shared=None, *, timeout):
    own = Path(own).resolve()
    shared = Path(shared).resolve() if shared else own
    temp = own / ".tmp"
    temp.mkdir(exist_ok=True)
    developer = next((d for d in _DEVELOPER_DIRS if os.path.isdir(d)), "")
    # A fresh environment: nothing of the operator's is passed through.
    env = {
        # The developer tools come before /usr/bin, whose stand-ins need more access.
        "PATH": ":".join(filter(None, ["/opt/homebrew/bin", developer and f"{developer}/usr/bin",
                                       "/usr/bin", "/bin", "/usr/sbin", "/sbin"])),
        "HOME": str(own), "TMPDIR": str(temp), "LANG": "en_US.UTF-8",
        "RUN_WORKSPACE": str(shared),
        **({"DEVELOPER_DIR": developer} if developer else {}),
    }
    return subprocess.run(_wrap(["/bin/sh", "-c", command], own, shared), cwd=own, env=env,
                          capture_output=True, text=True, errors="replace", timeout=timeout)
