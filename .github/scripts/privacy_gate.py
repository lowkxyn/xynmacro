"""Block publication of private identifiers and credentials.

The identifiers live outside the repository in the owner's annotated audit list:
``LABEL: value`` lines with uppercase labels, an optional ``IDENTIFIERS`` heading,
``#`` comments and blank lines. A label may carry an explanatory annotation,
``LABEL (note): value``. A label with no value takes the ``- value`` items listed
under it. Variants may share a line separated by ``,``, ``;``, ``|`` or ``/``,
and a parenthetical containing letters is an explanatory note, not part of the
value. Path labels are the exception: their slashes are kept and every path form
written on the line, notes included, is a variant:

    EMAIL_ALT: alt.person@example.net (note about where it appeared)
    PHONES:
    - +1 (555) 010-4477 (old mobile)
    - 020 7946 0958 / 02079460958
    POSTCODE_AREA: ZX9 8QW / ZX9 / Fictionton
    UNIVERSITY: Fictional State University / FSU / North Campus (campus)
    USER_PATH: C:\\Users\\Someone  (and /c/Users/Someone, \\Users\\Someone)

The label decides how a value is matched (EMAIL, PHONE, POSTCODE/ZIP, PATH);
everything else, such as names and each university alias, is a term matched on
its own. A malformed line, an empty label or a value that cannot be matched
safely stops the gate with its line number. Nothing private is dropped silently.

Phones match on their last nine digits with ordinary separators, an optional
international or trunk prefix and no digit or letter either side, so a hash that
contains the digits is not a hit; a number written another way can be missed.

A full postcode blocks anywhere. A short area code or a district name in a
postcode entry is shared by many people and appears in hashes and ordinary
geography, so it blocks only on a line that also names an address, home,
postcode, location or similar within 80 characters.

HANDLES, BIRTH_YEAR hints and MACHINE names shorter than 8 characters are context
for a human audit, not private on their own: a handle is a public account name, a
year is four common digits and a short machine name is a common word. Machine
entries with spaces or wildcards (``DESKTOP-xxxx``) are audit instructions. These
are counted but never block. Longer machine names block as terms.

Content is scanned as UTF-8 and, when it contains NUL bytes, also as UTF-16 LE
and BE at both byte alignments, so an identifier embedded in a binary or mixed
file is still found. Identifiers and credentials block on an exact match in any
view; review candidates never block without --strict.

Findings print as ``severity  category  object  path:line``. Categories are fixed
names, the matched text is never printed and any path that is itself a hit is
shown as ``<withheld path>``, so CI logs cannot leak what the gate protects.

    privacy_gate.py --identifiers FILE [--rev REV ...]   # reachable Git history
    privacy_gate.py --identifiers FILE --worktree DIR    # an export before commit
    privacy_gate.py --identifiers FILE --worktree DIR --allow ALLOW_FILE

A built installer carries third-party files that trip the gate by coincidence: a
library binary containing a short term, or a credential pattern in a library's
documentation example. The allow file pins each one as ``sha256  category  path``
(path relative to DIR, ``#`` comments allowed). A BLOCK is downgraded to ALLOWED
only when the file's bytes still hash to the pinned value and the category
matches, so a changed or new file blocks again. Allow files hold no private
values and apply to --worktree scans only.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys

CREDENTIALS = {
    "credential:discord-webhook": r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]{20,}",
    "credential:github-token": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{40,})",
    "credential:aws-key": r"\bAKIA[0-9A-Z]{16}\b",
    "credential:slack-token": r"\bxox[abprs]-[\w-]{10,}",
    "credential:api-secret-key": r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}",
    "credential:private-key": r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    # No backslashes: a Windows path glued to a scheme ("tauri://window-createdC:\Users\...")
    # in a compiled binary isn't a URL with a password.
    "credential:url-password": r"\b[a-z][a-z0-9+.-]*://[^/\\\s:@]+:[^/\\\s@]{3,}@",
}
CREDENTIALS = {label: re.compile(p) for label, p in CREDENTIALS.items()}

# Uncertain: could be the owner, a contributor, or a dependency credit. Reported
# for review; they fail the gate only with --strict.
CANDIDATES = {
    "review:email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b"),
    "review:profile-path": re.compile(
        r"(?i)\b[a-z]:(?:\\\\|\\|/)+users(?:\\\\|\\|/)+([^\\/\s\"'<>:*?|,;)\]]+)"),
}
# Fixtures and placeholders known to be fictional or public. Exact values only.
KNOWN_PUBLIC = {
    "syntheticuser", "sampleuser", "public", "default", "<user>", "%user%",
    "runneradmin", "noreply@github.com",
}
KNOWN_PUBLIC_EMAIL_DOMAINS = ("users.noreply.github.com", "example.com", "example.org")

WITHHELD = "<withheld path>"
# "LABEL: value" or "LABEL (explanatory annotation): value".
LABEL_LINE = re.compile(r"([A-Z][A-Z0-9_ ]*?)\s*(?:\([^()]*\))?\s*:(.*)")
NOTE = re.compile(r"\s*[(\[][^()\[\]]*[A-Za-z][^()\[\]]*[)\]]")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
# A path keeps its slashes: "C:\Users\x", "C:/Users/x", "/c/Users/x", "\Users\x".
PATH_TOKEN = re.compile(r"(?:[A-Za-z]:)?(?:[\\/]+[^\\/\s,;|()\"'`]+)+")
LOOKS_LIKE_PATH = re.compile(r"^(?:[A-Za-z]:)?[\\/]|\\")
EXAMPLE_PREFIX = re.compile(r"(?i)^(?:e\.?g\.?|i\.?e\.?|such as)\s+")
PLACEHOLDER = re.compile(r"(?i)x{3,}|\?{2,}|\*")
SHORT_MACHINE_NAME = 8
PHONE_SEP = r"[\s().-]*"
# Hits need a non-alphanumeric neighbour, so a value never matches inside a word,
# hash or signature that merely contains it.
EDGE_BEFORE, EDGE_AFTER = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
# Words that make a short area code or a place name personal on the same line.
LOCATION_CONTEXT = (EDGE_BEFORE + r"(?:address(?:es|ed)?|addr|post\s?codes?|postal|zip"
                    r"|li(?:ve[sd]?|ving)|home|resid\w*|locat\w*|based|near|area|district"
                    r"|borough|town|city|street|road|flat|personal)" + EDGE_AFTER)
LOCATION_REACH = 80


def load_identifiers(path):
    """Return ([(category, pattern)], contextual count); exits on any problem."""
    if not path or not os.path.isfile(path):
        sys.exit("privacy gate: private identifier file is required and was not found")
    with open(path, encoding="utf-8-sig") as fh:
        lines = [line.strip() for line in fh]
    entries = []  # [label, line number, [(line number, value)]]
    for number, line in enumerate(lines, 1):
        if not line or line.startswith("#") or line.rstrip(":").upper() == "IDENTIFIERS":
            continue
        if line[0] in "-*\u2022":
            if not entries:
                sys.exit(f"privacy gate: list item on line {number} has no label above it")
            entries[-1][2].append((number, line[1:].strip()))
            continue
        match = LABEL_LINE.fullmatch(line)
        if not match:
            sys.exit(f"privacy gate: line {number} is not 'LABEL: value'")
        label, value = match.group(1).strip(), match.group(2).strip()
        entries.append([label, number, [(number, value)] if value else []])

    patterns, contextual = [], 0
    for label, start, values in entries:
        key = re.sub(r"[\s_]+", "_", label)
        if not values:
            sys.exit(f"privacy gate: {key} on line {start} has no value")
        for number, value in values:
            variants = split_variants(key, value)
            if not variants:
                sys.exit(f"privacy gate: line {number} has a note but no value")
            for variant in variants:
                if _is_contextual(key, variant):
                    contextual += 1
                    continue
                found = identifier_patterns(key, variant)
                if not found:
                    sys.exit(f"privacy gate: a {key} value on line {number} "
                             "cannot be matched safely")
                patterns += found
    if not patterns:
        sys.exit("privacy gate: identifier file has no blocking entries")
    return patterns, contextual


def split_variants(key, value):
    """The alternatives on one line, by what the label says they are.

    Paths keep their slashes, and the forms listed in a path's note are paths too.
    Anything else is notes removed, then split on , ; | and / ("A / B / C").
    """
    if "PATH" in key:
        return [p.rstrip(".:") for p in PATH_TOKEN.findall(value) if p.rstrip(".:")]
    value = NOTE.sub("", value)
    if LOOKS_LIKE_PATH.search(value):
        parts = re.split(r"[,;|]|\s+/\s+", value)
    else:
        parts = re.split(r"[,;|/]", value)
    variants = [EXAMPLE_PREFIX.sub("", p.strip().strip("\"'`").strip()) for p in parts]
    return [v for v in variants if v]


def _is_contextual(key, value):
    if "HANDLE" in key or "BIRTH_YEAR" in key:
        return True
    # A hostname has no spaces and no wildcards: "check for any PC hostname" and
    # "DESKTOP-xxxx" are instructions for the audit, not names.
    return "MACHINE" in key and (len(value) < SHORT_MACHINE_NAME or " " in value
                                 or PLACEHOLDER.search(value) is not None)


def identifier_patterns(key, value):
    """[(category, pattern)] for one value, or [] if it cannot be matched safely."""
    emails = EMAIL.findall(value)
    if "EMAIL" in key or emails:
        found = [("identifier:email", re.compile(
            r"(?<![\w.+-])" + re.escape(e) + r"(?![\w-])", re.IGNORECASE)) for e in emails]
        # "Name <email>": the name is private too.
        annotated = re.fullmatch(r"(.+?)\s*<[^<>\s]+@[^<>\s]+>", value)
        if found and annotated:
            found += identifier_patterns("NAME", annotated.group(1))
        return found
    digits = re.sub(r"\D", "", value)
    if any(k in key for k in ("PHONE", "MOBILE", "TEL")) or (
            len(digits) >= 7 and re.fullmatch(r"[\d\s().+-]+", value)):
        if len(digits) < 7 or not re.fullmatch(r"[\d\s().+/-]+", value):
            return []
        return [("identifier:phone", _phone_pattern(digits))]
    if "PATH" in key or "\\" in value or "/" in value:
        parts = [p for p in re.split(r"[\\/]+", value) if p]
        # A bare "C:\Users" would match every profile path; the folder must be named.
        if len(parts) < 2 or parts[-1].lower() in ("users", "home") or parts[-1].endswith(":"):
            return []
        return [("identifier:path", re.compile(
            EDGE_BEFORE + r"(?:\\\\|\\|/)+".join(map(re.escape, parts)) + EDGE_AFTER,
            re.IGNORECASE))]
    if any(k in key for k in ("POSTCODE", "POSTAL", "ZIP")):
        chars = re.sub(r"[^A-Za-z0-9]", "", value)
        if len(chars) >= 5 and re.search(r"\d", chars):
            return [("identifier:postcode", re.compile(
                EDGE_BEFORE + r"[\s-]?".join(chars) + EDGE_AFTER, re.IGNORECASE))]
        # An outward area code ("ZX9") or a district name is shared by many people
        # and turns up in hashes and ordinary geography: it blocks only on a line
        # that also talks about an address, a home or a location.
        if len(chars) < 2:
            return []
        words = [re.escape(w) for w in re.split(r"[\s_.-]+", value) if w]
        term = EDGE_BEFORE + r"[\s_.-]*".join(words) + EDGE_AFTER
        return [("identifier:location", re.compile(
            f"{LOCATION_CONTEXT}.{{0,{LOCATION_REACH}}}?{term}"
            f"|{term}.{{0,{LOCATION_REACH}}}?{LOCATION_CONTEXT}", re.IGNORECASE))]
    words = [re.escape(w) for w in re.split(r"[\s_.-]+", value) if w]
    if len("".join(words)) < 3:
        return []
    # Short all-caps aliases are matched in that case only, so an acronym does not
    # fire on the same letters in an ordinary lowercase word.
    flags = 0 if value.isupper() and len(value) <= 5 else re.IGNORECASE
    return [("identifier:term", re.compile(
        EDGE_BEFORE + r"[\s_.-]*".join(words) + EDGE_AFTER, flags))]


def _phone_pattern(digits):
    # The last nine digits are the subscriber number in national and international
    # form alike, so "+44 (0)7700 900123", "0044 7700 900123" and "07700 900123"
    # all match. Up to five leading country, trunk or area digits are allowed.
    core = digits[-9:]
    return re.compile(r"(?<![0-9A-Za-z+])(?:\+|00)?(?:\d" + PHONE_SEP + r"){0,5}"
                      + PHONE_SEP.join(core) + EDGE_AFTER)


def text_views(data):
    """Yield (suffix, text) for every encoding an identifier could hide in."""
    yield "", data.decode("utf-8", errors="replace")
    if b"\0" in data:
        for codec in ("utf-16-le", "utf-16-be"):
            for offset in (0, 1):
                chunk = data[offset:]
                yield f" [{codec}]", chunk[:len(chunk) - len(chunk) % 2].decode(
                    codec, errors="replace")


def _is_known_public(label, text):
    lowered = text.lower()
    if lowered in KNOWN_PUBLIC:
        return True
    return label == "review:email" and lowered.endswith(KNOWN_PUBLIC_EMAIL_DOMAINS)


def scan_text(text, identifiers, where, findings):
    """Append (severity, category, where, line) per hit. Values never leave here."""
    for number, line in enumerate(text.splitlines() or [""], 1):
        for label, pattern in identifiers:
            if pattern.search(line):
                findings.append(("BLOCK", label, where, number))
        for label, pattern in CREDENTIALS.items():
            if pattern.search(line):
                findings.append(("BLOCK", label, where, number))
        for label, pattern in CANDIDATES.items():
            for match in pattern.finditer(line):
                hit = match.group(match.lastindex or 0)
                if not _is_known_public(label, hit):
                    findings.append(("REVIEW", label, where, number))
                    break


def scan_bytes(data, identifiers, where, findings):
    for suffix, text in text_views(data):
        scan_text(text, identifiers, where + suffix, findings)


def _git(repo, *args, data=None):
    return subprocess.run(["git", "-C", repo, *args], input=data, check=True,
                          capture_output=True).stdout


def _headers(body):
    head = body.split(b"\n\n", 1)[0]
    return dict(line.split(b" ", 1) for line in head.splitlines() if b" " in line)


def scan_git(repo, revs, identifiers):
    findings = []
    listing = _git(repo, "rev-list", "--objects", *revs).decode("utf-8", "replace")
    oids = dict.fromkeys(row.split(" ", 1)[0] for row in listing.splitlines() if row)
    # Annotated tags are not walked by rev-list --objects unless named directly.
    tags = _git(repo, "for-each-ref", "--format=%(objectname) %(objecttype)", "refs/tags")
    for row in tags.decode().splitlines():
        oid, kind = row.split()
        if kind == "tag":
            oids.setdefault(oid)
    objects = {}
    batch = _git(repo, "cat-file", "--batch", data="\n".join(oids).encode() + b"\n")
    pos = 0
    while pos < len(batch):
        header_end = batch.index(b"\n", pos)
        oid, kind, size = batch[pos:header_end].decode().split()
        objects[oid] = (kind, batch[header_end + 1:header_end + 1 + int(size)])
        pos = header_end + 1 + int(size) + 1

    # rev-list names each blob by one path only, so the same bytes committed again
    # under another name would go unseen. Every path of every root tree is listed.
    roots = set()
    for kind, body in objects.values():
        headers = _headers(body) if kind in ("commit", "tag") else {}
        if kind == "commit":
            roots.add(headers[b"tree"].decode())
        elif headers.get(b"type") == b"tree":
            roots.add(headers[b"object"].decode())
    paths, blob_paths = {}, {}
    for root in sorted(roots):
        for record in _git(repo, "ls-tree", "-r", "-t", "-z", root).split(b"\0"):
            if not record:
                continue
            meta, _, name = record.partition(b"\t")
            _, kind, oid = meta.decode().split()
            path = name.decode("utf-8", "replace")
            paths.setdefault(path, oid)
            if kind == "blob":
                blob_paths.setdefault(oid, set()).add(path)
    withheld = set()
    for path, oid in paths.items():
        before = len(findings)
        scan_text(path, identifiers, f"{oid[:12]} filename", findings)
        if len(findings) > before:
            withheld.add(path)
    refs = _git(repo, "for-each-ref", "--format=%(refname)").decode("utf-8", "replace")
    for ref in refs.splitlines():
        scan_text(ref, identifiers, "ref name", findings)

    for oid, (kind, body) in objects.items():
        if kind in ("commit", "tag"):
            # Author/committer/tagger headers and the message are all in the body.
            scan_bytes(body, identifiers, f"{oid[:12]} {kind}", findings)
        elif kind == "blob":
            names = blob_paths.get(oid)
            safe = sorted(names - withheld) if names else []
            shown = safe[0] if safe else WITHHELD if names else "(tagged blob)"
            scan_bytes(body, identifiers, f"{oid[:12]} {shown}", findings)
    return findings


def load_allow_list(path):
    """{(relative path, category): sha256} from "sha256  category  path" lines."""
    allowed = {}
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            if len(parts) != 3 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
                sys.exit(f"privacy gate: allow file line {number} is not 'sha256  category  path'")
            digest, category, rel = parts
            allowed[(rel.replace("\\", "/"), category)] = digest
    return allowed


def _apply_allow(findings, start, rel, digest, allowed, notes):
    """Downgrade this file's BLOCK findings that the allow list pins to its exact bytes."""
    for index in range(start, len(findings)):
        severity, label, where, line = findings[index]
        pinned = allowed.get((rel, label))
        if severity != "BLOCK" or pinned is None:
            continue
        if pinned == digest:
            findings[index] = ("ALLOWED", label, where, line)
        else:
            notes.add(f"allow entry no longer matches the file, still blocking: {rel} {label}")


def scan_worktree(root, identifiers, allowed=None):
    findings = []
    notes, used = set(), set()
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "target")]
        for name in files:
            # Linked worktrees use a .git pointer file containing a local path.
            # Git never publishes its administrative metadata as source.
            if name == ".git":
                continue
            full = os.path.join(folder, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            before = len(findings)
            scan_text(rel, identifiers, "worktree filename", findings)
            # A path that is itself a hit is withheld from the content findings too.
            shown = rel if len(findings) == before else WITHHELD
            with open(full, "rb") as fh:
                data = fh.read()
            start = len(findings)
            scan_bytes(data, identifiers, f"worktree {shown}", findings)
            if allowed and shown == rel and len(findings) > start:
                _apply_allow(findings, start, rel, hashlib.sha256(data).hexdigest(),
                             allowed, notes)
                used.update(key for key in allowed if key[0] == rel)
    for rel, label in sorted(set(allowed or ()) - used):
        notes.add(f"allow entry matched nothing: {rel} {label}")
    for note in sorted(notes):
        print(f"note: {note}")
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--identifiers", required=True, help="private LABEL: value file")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--rev", action="append", help="revisions to scan (default: --all)")
    parser.add_argument("--worktree", help="scan this directory instead of Git history")
    parser.add_argument("--strict", action="store_true", help="fail on review candidates too")
    parser.add_argument("--allow", help="pinned third-party hits (--worktree only)")
    args = parser.parse_args(argv)
    if args.allow and not args.worktree:
        parser.error("--allow applies to --worktree scans only")

    identifiers, contextual = load_identifiers(args.identifiers)
    if args.worktree:
        allowed = load_allow_list(args.allow) if args.allow else None
        findings = scan_worktree(args.worktree, identifiers, allowed)
    else:
        findings = scan_git(args.repo, args.rev or ["--all"], identifiers)

    seen = set()
    for finding in findings:
        if finding not in seen:
            seen.add(finding)
            severity, label, where, line = finding
            print(f"{severity}  {label}  {where}:{line}")
    blocked = any(s == "BLOCK" or (args.strict and s == "REVIEW") for s, *_ in findings)
    allowed_count = sum(s == "ALLOWED" for s, *_ in findings)
    print(f"privacy gate: {'FAILED' if blocked else 'passed'} "
          f"({sum(s == 'BLOCK' for s, *_ in findings)} blocking, "
          f"{sum(s == 'REVIEW' for s, *_ in findings)} for review, "
          f"{allowed_count} allowed by pinned hash; "
          f"{contextual} contextual entries not blocking)")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
