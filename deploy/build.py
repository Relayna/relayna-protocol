"""Build the development images from the exact component revisions in sources.json."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile

DEPLOY = Path(__file__).resolve().parent
IMAGES = {
    "gateway": ("gateway", "Dockerfile", "RELAYNA_GATEWAY_IMAGE"),
    "spooltail": ("spooltail", "Dockerfile", "RELAYNA_SPOOLTAIL_IMAGE"),
    "sdk": ("sdk", "Dockerfile.reference", "RELAYNA_SDK_IMAGE"),
    "studio-backend": ("sdk", "studio/backend/Dockerfile", "RELAYNA_STUDIO_BACKEND_IMAGE"),
    "studio": ("sdk", "apps/studio/Dockerfile", "RELAYNA_STUDIO_IMAGE"),
}


def init_env():
    path = DEPLOY / ".env"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"Using existing {path}")
        return
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        print(f"RELAYNA_DB_PASSWORD={secrets.token_hex(24)}", file=output)
        for name in ("RELAYNA_GATEWAY_ADMIN_TOKEN", "RELAYNA_STUDIO_OPERATOR_TOKEN"):
            print(f"{name}=op_live_{secrets.token_hex(24)}", file=output)
    print(f"Created {path}")


def run(*args, cwd=None):
    subprocess.run(args, check=True, cwd=cwd)


def checkout(directory, source):
    commit = source["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"Expected a full Git commit for {source['repository']}")
    directory.mkdir()
    run("git", "init", "-q", cwd=directory)
    run("git", "fetch", "-q", "--depth=1", source["repository"], commit, cwd=directory)
    run("git", "checkout", "-q", "--detach", commit, cwd=directory)


def build(names, lock):
    sources = json.loads(lock.read_text(encoding="utf-8"))
    needed = {IMAGES[name][0] for name in names}
    if "sdk" in names:
        needed.add("spooltail")
    with tempfile.TemporaryDirectory(prefix="relayna-build-") as directory:
        work = Path(directory)
        for component in sorted(needed):
            checkout(work / component, sources[component])
        for name in names:
            component, dockerfile, variable = IMAGES[name]
            source = sources[component]
            image = os.getenv(variable, f"relayna-local/{name}:reference")
            context = work / component
            build_args = []
            if name == "sdk":
                context = DEPLOY
                build_args = [
                    "--build-context", f"sdk={work / 'sdk'}",
                    "--build-context", f"spooltail={work / 'spooltail'}",
                    "--label", f"org.relayna.spooltail.revision={sources['spooltail']['commit']}",
                ]
            run(
                "docker", "buildx", "build", "--load", "-t", image, "-f", str(context / dockerfile),
                "--label", f"org.opencontainers.image.source={source['repository']}",
                "--label", f"org.opencontainers.image.revision={source['commit']}",
                *build_args, str(context),
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init-env", action="store_true", help="Create the local Compose credentials if absent")
    parser.add_argument("--lock", type=Path, default=DEPLOY / "sources.json")
    parser.add_argument("--image", action="append", choices=IMAGES, help="Build only this image; repeat as needed")
    args = parser.parse_args()
    if args.init_env:
        init_env()
    else:
        build(args.image or list(IMAGES), args.lock)


if __name__ == "__main__":
    main()
