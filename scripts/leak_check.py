#!/usr/bin/env python3
"""Fail when a change adds private context to a public repository.

The patterns come from the LEAK_PATTERNS secret (one Python regex per line,
`#` comments allowed), so the list itself is never published. A hit is
reported by pattern number and location only: printing the matched text
would put it in a public Actions log.

Scanned: lines added by the change, file names, commit messages, and for a
pull request its title and description. Co-authored-by and Signed-off-by
lines naming a GitHub noreply address count as commit identity: a match
against the patterns only warns, because the account's public name is
expected there. Two identity rules fail on their own, without the patterns:
an address on a machine-local domain (`user@host.local`, `.localdomain`,
`.lan`, `.internal`, or no domain at all) anywhere, and, on a pull request
from a branch of the repository itself, any address that is not a noreply
one (see NOREPLY), since a box's own mail domain says where it runs. Paths listed in .leakcheck-allow
(fnmatch globs, one per line, read from the BASE commit so a change cannot
exempt itself) are skipped for file content. New images and videos only
warn: no pattern can read them, so they need a look by eye.

Locally, `LEAK_PATTERNS="$(cat patterns.txt)" leak_check.py --tree [--show]`
scans every tracked file instead.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys


# Only GitHub's own credit lines count as identity: a plain name (letters,
# spaces, . ' -) and a users.noreply.github.com address, nothing else on the
# line, so the exempt part can hold no more than the commit author field does. Any other
# co-author or sign-off line is scanned like the rest of the message.
CREDIT_LINE = re.compile(
    r"^\s*(co-authored-by|signed-off-by): [A-Za-z][A-Za-z .'-]{0,59} <[0-9]+\+[A-Za-z0-9-]+@users\.noreply\.github\.com>\s*$",
    re.I)


# An identity address on a domain only a machine knows: a git default built
# from the login and host name, which names the computer it was made on.
MACHINE_LOCAL = re.compile(
    r"@(?:[^@>\s]*\.)?(?:local|localdomain|lan|internal|home|home\.arpa|localhost)$|@[^.@>\s]+$", re.I)

# Addresses that say nothing about where a commit was made.
NOREPLY = re.compile(
    r"(?:@users\.noreply\.github\.com|^noreply@github\.com|^support@github\.com"
    r"|^noreply@anthropic\.com|^noreply@openai\.com)$", re.I)

IDENTITY_LINE = re.compile(r"<([^<>\s]+)>\s*$")


def identity_problems(idents, own_branch):
    """Errors for (label, address) pairs: machine-local always, and any
    non-noreply address when the change comes from the repository itself."""
    out = []
    for label, addr in idents:
        if MACHINE_LOCAL.search(addr):
            out.append(f"{label} uses a machine-local address; set user.email to the account's noreply address")
        elif own_branch and not NOREPLY.search(addr):
            out.append(f"{label} is not a noreply address; set user.email to the account's noreply address")
    return out


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, errors="replace").stdout


def has_commit(sha):
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True).returncode == 0


def present_base(base, base_ref, head):
    """The event's base commit, or the nearest stand-in the checkout has. A
    pull request event can name a base the clone never fetched (the base
    branch moved on, or the base was rebased away); rather than crash, fetch
    it, and failing that diff from where the head left the base branch."""
    if has_commit(base):
        return base
    subprocess.run(["git", "fetch", "-q", "--no-tags", "origin", base], capture_output=True)
    if has_commit(base):
        return base
    if base_ref:
        subprocess.run(["git", "fetch", "-q", "--no-tags", "origin", base_ref], capture_output=True)
        mb = subprocess.run(["git", "merge-base", "FETCH_HEAD", head], capture_output=True, text=True)
        if mb.returncode == 0 and mb.stdout.strip():
            print(f"::warning::base commit {base[:10]} is not in the checkout; checking from the merge base with {base_ref}")
            return mb.stdout.strip()
    return base


def load_patterns(raw):
    out = []
    for n, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append((n, re.compile(line)))
        except re.error as e:
            print(f"::warning::LEAK_PATTERNS line {n} is not a valid regex ({e}); skipped")
    return out


def load_allow(ref=None, path=".leakcheck-allow"):
    """Exclusion globs. For a change, read them from the base commit, so a
    pull request can never widen its own exclusions; locally, from the tree."""
    if ref:
        try:
            text = git("show", f"{ref}:{path}")
        except subprocess.CalledProcessError:
            return []
    elif os.path.exists(path):
        with open(path) as f:
            text = f.read()
    else:
        return []
    return [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]


def added_lines(base, head):
    """(path, line_no, text) for every line the change adds."""
    diff = git("diff", "--no-color", "--unified=0", "--no-ext-diff", f"{base}...{head}")
    path, line_no = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            line_no = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            yield path, line_no, line[1:]
            line_no += 1


def scan_tree(patterns, show):
    """Local use: every tracked file at HEAD. `show` prints the matched text
    too (never do that in a public log)."""
    allow = load_allow()
    total = 0
    for path in git("ls-files").splitlines():
        if any(fnmatch.fnmatch(path, g) for g in allow):
            continue
        for n, rx in patterns:
            if rx.search(path):
                total += 1
                print(f"{path}: file name, rule line {n}")
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        for i, text in enumerate(lines, 1):
            for n, rx in patterns:
                m = rx.search(text)
                if m:
                    total += 1
                    print(f"{path}:{i}: rule line {n}" + (f": {m.group(0)!r} in {text.strip()[:160]}" if show else ""))
    print(f"{total} hit(s)")
    return 1 if total else 0


def main():
    patterns = load_patterns(os.environ.get("LEAK_PATTERNS", ""))
    if "--tree" in sys.argv:
        if not patterns:
            print("LEAK_PATTERNS is empty")
            return 2
        return scan_tree(patterns, "--show" in sys.argv)
    if not patterns:
        # Fail closed: a check that silently scanned nothing would read as a pass.
        print("::error::LEAK_PATTERNS is empty or unavailable to this run, so nothing could be checked."
              " Callers must pass the organisation secret and run on pull_request_target or push.")
        return 1
    event = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
    texts = []  # (where, text)
    pr = event.get("pull_request")
    if pr:
        base, head = pr["base"]["sha"], pr["head"]["sha"]
        base = present_base(base, pr["base"].get("ref"), head)
        texts.append(("PR title", pr.get("title") or ""))
        texts.append(("PR description", pr.get("body") or ""))
    else:
        base, head = event.get("before"), event.get("after")
        if not base or set(base) == {"0"}:
            base = git("rev-list", "--max-parents=0", head).split()[0]
    credits = {}  # sha -> co-author/sign-off lines, judged like the commit identity
    for sha in git("rev-list", f"{base}..{head}").split():
        body = []
        for line in git("log", "-1", "--format=%B", sha).splitlines():
            if CREDIT_LINE.match(line):
                credits.setdefault(sha, []).append(line)
            else:
                body.append(line)
        texts.append((f"commit {sha[:10]} message", "\n".join(body)))
    for path in git("diff", "--name-only", f"{base}...{head}").splitlines():
        texts.append(("file name", path))
    allow = load_allow(base)

    hits = []
    # Commit identities matching the patterns only warn: they come from each machine's git config,
    # or from GitHub's squash-merge co-author lines, not from what the change says.
    # A pull request from a branch of this repository is the organisation's
    # own work, so its identities must be noreply ones; a fork's contributor
    # chooses their own address.
    own_branch = bool(pr) and (pr["head"].get("repo") or {}).get("full_name") == (pr["base"].get("repo") or {}).get("full_name")
    for sha in git("rev-list", f"{base}..{head}").split():
        ident = "\n".join([git("log", "-1", "--format=%an <%ae> / %cn <%ce>", sha)] + credits.get(sha, []))
        if any(rx.search(ident) for _, rx in patterns):
            print(f"::warning::commit {sha[:10]}: author or committer identity matches a private pattern")
        idents = [("author", git("log", "-1", "--format=%ae", sha).strip()),
                  ("committer", git("log", "-1", "--format=%ce", sha).strip())]
        for line in git("log", "-1", "--format=%B", sha).splitlines():
            if re.match(r"^\s*(co-authored-by|signed-off-by):", line, re.I):
                m = IDENTITY_LINE.search(line)
                if m:
                    idents.append(("co-author or sign-off line", m.group(1)))
        for problem in identity_problems(idents, own_branch):
            hits.append(f"commit {sha[:10]}: {problem}")
    for where, text in texts:
        for n, rx in patterns:
            if rx.search(text):
                hits.append(f"rule on line {n} of LEAK_PATTERNS, in {where}")
    images = []
    for path, line_no, text in added_lines(base, head):
        if any(fnmatch.fnmatch(path, g) for g in allow):
            continue
        for n, rx in patterns:
            if rx.search(text):
                hits.append(f"rule on line {n} of LEAK_PATTERNS, at {path}:{line_no}")
    for path in git("diff", "--name-only", "--diff-filter=A", f"{base}...{head}").splitlines():
        if re.search(r"\.(png|jpe?g|gif|webp|heic|mp4|mov)$", path, re.I):
            images.append(path)

    for p in images:
        print(f"::warning file={p}::new image or video: check by eye that it shows only made-up data")
    if hits:
        for h in hits:
            print(f"::error::{h}")
        print(f"{len(hits)} private-context hit(s). The matched text is not printed (this log is public);"
              " look at the named places, take the private detail out (see the repo's CLAUDE.md), and push again.")
        return 1
    print(f"No private context found ({len(patterns)} patterns, {len(texts)} texts checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
