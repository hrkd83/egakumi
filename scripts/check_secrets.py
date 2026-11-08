#!/usr/bin/env python3
"""Check changed Git blobs for recognizable private keys and token prefixes."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

MAX_BLOB_BYTES = 2 * 1024 * 1024
RULES = (
    ("private-key", re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----[ \t]*(?:\r?\n|\\r\\n|\\n)[A-Za-z0-9+/]{32,}={0,2}")),
    ("github-token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,255}|github_pat_[A-Za-z0-9_]{40,255})\b")),
    ("openai-token", re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,255}\b")),
    ("slack-token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,255}\b")),
)


class SecretCheckError(ValueError):
    """Fixed errors never include Git output or user-supplied values."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SecretCheckError()


def git(*arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30, check=False,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SecretCheckError() from None
    if result.returncode:
        raise SecretCheckError()
    return result.stdout


def commit(reference: str) -> str:
    value = git("rev-parse", "--verify", "--end-of-options", reference + "^{commit}").strip()
    if re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", value) is None:
        raise SecretCheckError()
    return value.decode("ascii")


def check(head: str, base: str | None = None) -> int:
    head = commit(head)
    changed = None
    if base is not None:
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base) is None:
            raise SecretCheckError()
        changed = set(git("diff", "--no-renames", "--name-only", "--diff-filter=ACMRT", "-z",
                          commit(base), head, "--").split(b"\0"))
    findings = inspected = skipped = item = 0
    for entry in git("ls-tree", "-r", "-z", "--full-tree", head).split(b"\0"):
        if not entry:
            continue
        try:
            header, path = entry.split(b"\t", 1)
            mode, kind, oid = header.split()
        except ValueError:
            raise SecretCheckError() from None
        if kind != b"blob" or (changed is not None and path not in changed):
            continue
        item += 1
        size = git("cat-file", "-s", oid.decode("ascii")).strip()
        if not size.isdigit():
            raise SecretCheckError()
        if int(size) > MAX_BLOB_BYTES:
            skipped += 1
            continue
        content = git("cat-file", "blob", oid.decode("ascii"))
        if b"\0" in content:
            skipped += 1
            continue
        inspected += 1
        for rule, pattern in RULES:
            for match in pattern.finditer(content):
                findings += 1
                if findings <= 100:
                    line = content.count(b"\n", 0, match.start()) + 1
                    print(f"secret-check: BLOCK rule={rule} item={item} line={line}")
    if findings:
        print(f"secret-check: BLOCK findings={findings}")
        return 1
    print(f"secret-check: PASS inspected={inspected} skipped={skipped}")
    return 0


def main(argv=None) -> int:
    try:
        parser = Parser(prog="check-secrets", description=__doc__)
        parser.add_argument("--base", help="Full base commit SHA; omit to inspect all head blobs.")
        parser.add_argument("--head", default="HEAD")
        arguments = parser.parse_args(argv)
        return check(arguments.head, arguments.base)
    except (SecretCheckError, UnicodeError, ValueError):
        print("secret-check: ERROR inspection failed")
        return 2


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(main())
