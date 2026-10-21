"""The gate is only useful if it finds every form of an identifier and never
prints one. All identifiers here are fictional, in the audit list's own format."""

import contextlib
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import privacy_gate  # noqa: E402

IDENTIFIERS = """IDENTIFIERS
# fictional values only
FULL_NAME: Pat Fictional
EMAIL: Pat Fictional <pat.fictional@example.net>
EMAIL_ALT: alt.person@example.net (note about where it appeared)
PHONES:
- +1 (555) 010-4477 (old mobile)
- +44 7700 900123
- 020 7946 0958 (national form, landline)
MOBILE: +447700900456 / 07700900456 / 7700900456
POSTCODE: ZX9 8QW, ZX98QW
POSTCODE_AREA: QY4 7HN / QY4 / Fictionton / Northvale
UNIVERSITY: Fictional State University; FSU (alias) / Mockford Institute / Elm Quarter (campus)
USER_PATHS: C:\\Users\\PatFictional, C:\\\\Users\\\\PatFictional (escaped in JSON)
WINDOWS_USER_PATH: D:\\Users\\PatAlt  (and /d/Users/PatAlt, D:/Users/PatAlt, \\Users\\PatAlt)
HANDLES (lower risk, but note where they link): patf, fictionalpat
BIRTH_YEAR_HINT: 1999   (also 1998 / 3 March)
MACHINE NAMES: check for any hostname, e.g. DESKTOP-xxxx, PAT-PC, PATFICTIONAL-DESKTOP1
"""
PRIVATE_TEXT = ("pat.fictional", "Pat Fictional", "PatFictional", "alt.person", "010-4477",
                "5550104477", "900123", "7946 0958", "79460958", "ZX9", "Fictional State",
                "DESKTOP1", "900456", "QY4", "Fictionton", "Northvale", "Mockford",
                "Elm Quarter", "PatAlt")


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ids = os.path.join(self.tmp, "ids.txt")
        self.write_ids(IDENTIFIERS)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_ids(self, text):
        with open(self.ids, "w", encoding="utf-8") as fh:
            fh.write(text)

    def run_gate(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = privacy_gate.main(["--identifiers", self.ids, *args])
        return code, out.getvalue()

    def write(self, root, name, text, encoding="utf-8"):
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(text, bytes):
            with open(path, "wb") as fh:
                fh.write(text)
        else:
            with open(path, "w", encoding=encoding, newline="") as fh:
                fh.write(text)

    def assert_never_printed(self, output):
        for value in PRIVATE_TEXT:
            self.assertNotIn(value.lower(), output.lower())


class TestIdentifierFile(GateTest):
    def found(self, text):
        patterns, _ = privacy_gate.load_identifiers(self.ids)
        return {label for label, p in patterns if p.search(text)}

    def test_missing_private_file_fails_clearly(self):
        with self.assertRaises(SystemExit) as raised:
            privacy_gate.main(["--identifiers", os.path.join(self.tmp, "absent.txt"),
                               "--worktree", self.tmp])
        self.assertIn("identifier file is required", str(raised.exception))

    def test_annotated_emails_ignore_their_notes(self):
        self.assertEqual(self.found("mail PAT.FICTIONAL@EXAMPLE.NET"),
                         {"identifier:email", "identifier:term"})
        self.assertEqual(self.found("cc alt.person@example.net."), {"identifier:email"})
        self.assertEqual(self.found("a note about where it appeared"), set())

    def test_three_phones_in_international_and_national_forms(self):
        for phone in ("555-010-4477", "+15550104477", "(555) 010 4477",
                      "+44 (0)7700 900 123", "07700900123", "0044 7700 900123",
                      "+44 20 7946 0958", "020-7946-0958"):
            self.assertEqual(self.found(f"call {phone}."), {"identifier:phone"}, phone)

    def test_hashes_containing_the_digits_are_not_phone_hits(self):
        self.assertEqual(self.found("sha256 ab15550104477cd 9f07700900123e"), set())

    def test_postcode_university_alias_name_and_path_variants(self):
        for text in ("zx9 8qw", "ZX98QW", "ZX9-8QW"):
            self.assertEqual(self.found(text), {"identifier:postcode"}, text)
        for text in ("Fictional State University", "fictional-state-university", "at FSU"):
            self.assertEqual(self.found(text), {"identifier:term"}, text)
        self.assertEqual(self.found("by Pat_Fictional"), {"identifier:term"})
        for path in (r"c:\users\patfictional\x", "C:/Users/PatFictional/x",
                     r'"C:\\Users\\PatFictional\\x"'):
            self.assertIn("identifier:path", self.found(path), path)
        self.assertNotIn("identifier:path", self.found(r"C:\Users\PatFictionality"))

    def test_slash_separated_alternatives_split_by_label(self):
        for phone in ("+44 7700 900456", "07700900456", "tel 7700 900 456"):
            self.assertEqual(self.found(phone), {"identifier:phone"}, phone)
        self.assertEqual(self.found("qy4 7hn"), {"identifier:postcode"})
        for alias in ("Mockford Institute", "mockford-institute", "the Elm Quarter site"):
            self.assertEqual(self.found(alias), {"identifier:term"}, alias)
        # The path note lists forms of the path; each is matched, slashes intact.
        for path in (r"D:\Users\PatAlt\x", "/d/Users/PatAlt/x", "D:/Users/PatAlt",
                     r"E:\Users\PatAlt", r'"D:\\Users\\PatAlt"'):
            self.assertIn("identifier:path", self.found(path), path)
        self.assertNotIn("identifier:path", self.found(r"C:\Users\Other\PatAlt2"))

    def test_area_codes_and_districts_block_only_in_personal_context(self):
        for text in ("home address: 4 Lane Road, Fictionton", "postcode QY4",
                     "I live in northvale", "Northvale is where I'm based"):
            self.assertEqual(self.found(text), {"identifier:location"}, text)
        for text in ("sha1 9fQY4e0 qy4", "QY4", "the Fictionton derby result",
                     "Northvale v Fictionton 2-1", "addressQY4x"):
            self.assertEqual(self.found(text), set(), text)

    def test_handles_birth_year_and_short_machine_names_are_context_only(self):
        _, contextual = privacy_gate.load_identifiers(self.ids)
        # 2 handles, 1 birth year, guidance text, a wildcard name and PAT-PC.
        self.assertEqual(contextual, 6)
        self.assertEqual(self.found("patf fictionalpat 1999 1998 PAT-PC fsu DESKTOP-xxxx"
                                    " check for any hostname"), set())
        self.assertIn("identifier:term", self.found("host PATFICTIONAL-DESKTOP1"))

    def test_annotated_label_parses_but_lowercase_or_unlabelled_lines_do_not(self):
        self.write_ids("HANDLES (lower: risk): secretvalue\nFULL_NAME: Pat Fictional\n")
        self.assertEqual(privacy_gate.load_identifiers(self.ids)[1], 1)
        for text in ("handles (note): secretvalue\n", "(note) FULL_NAME: secretvalue\n",
                     "WINDOWS_USER_PATH: secretvalue (no path here)\n",
                     "WINDOWS_USER_PATH: C:\\Users\n",
                     "POSTCODE_AREA: Q / ZX9 8QW\n"):
            self.write_ids(text)
            with self.assertRaises(SystemExit) as raised:
                privacy_gate.load_identifiers(self.ids)
            self.assertNotIn("secretvalue", str(raised.exception), text)

    def test_malformed_or_empty_entries_stop_without_echoing_values(self):
        for text in ("EMAIL: secretvalue without an address\n",
                     "PHONES:\nEMAIL: q@example.net\n",
                     "secretvalue@example.net\n",
                     "email: secretvalue@example.net\n",
                     "PHONE: 12-34 secretvalue\n",
                     "- secretvalue\n",
                     "FULL_NAME: (only a note about secretvalue)\n",
                     "HANDLES: secretvalue\nBIRTH_YEAR_HINT: 1999\n"):
            self.write_ids(text)
            with self.assertRaises(SystemExit) as raised:
                privacy_gate.load_identifiers(self.ids)
            self.assertNotIn("secretvalue", str(raised.exception), text)


class TestWorktree(GateTest):
    def test_linked_git_pointer_is_metadata_but_source_still_blocks(self):
        root = os.path.join(self.tmp, "linked")
        self.write(root, ".git", "gitdir: C:/Users/Pat Fictional/private/.git/worktrees/linked\n")
        self.assertEqual(self.run_gate("--worktree", root)[0], 0)
        self.write(root, "source.txt", "Pat Fictional\n")
        code, output = self.run_gate("--worktree", root)
        self.assertEqual(code, 1)
        self.assertIn("source.txt", output)
        self.assert_never_printed(output)

    def test_finds_utf16_files_and_filenames_without_printing_values(self):
        root = os.path.join(self.tmp, "export")
        self.write(root, "notes.txt", "ok\ncall +1 555 010 4477\n", encoding="utf-16")
        self.write(root, "from Pat Fictional/readme.md", "write to alt.person@example.net\n")
        code, output = self.run_gate("--worktree", root)
        self.assertEqual(code, 1)
        self.assertIn("BLOCK  identifier:phone  worktree notes.txt [utf-16-le]:2", output)
        self.assertIn("BLOCK  identifier:term  worktree filename:1", output)
        self.assertIn(f"BLOCK  identifier:email  worktree {privacy_gate.WITHHELD}:1", output)
        self.assert_never_printed(output)

    def test_identifier_embedded_as_utf16_in_mixed_binary(self):
        root = os.path.join(self.tmp, "export")
        # Binary header, an odd-aligned UTF-16 LE string, a UTF-16 BE string, then UTF-8.
        payload = (b"\x89BIN\x00\x01\x02\xff\x07"
                   + "mail pat.fictional@example.net".encode("utf-16-le")
                   + b"\x00\x00\xfe" + "tel 07700 900123".encode("utf-16-be")
                   + b"\x00\nplain utf-8 tail\n")
        self.write(root, "blob.bin", payload)
        code, output = self.run_gate("--worktree", root)
        self.assertEqual(code, 1)
        self.assertIn("identifier:email  worktree blob.bin [utf-16-le]", output)
        self.assertIn("identifier:phone  worktree blob.bin [utf-16-be]", output)
        self.assert_never_printed(output)

    def test_binary_noise_is_not_a_hit(self):
        root = os.path.join(self.tmp, "export")
        self.write(root, "noise.bin", bytes(range(256)) * 8
                   + "Fictionality patfish".encode("utf-16-le"))
        code, output = self.run_gate("--worktree", root)
        self.assertEqual(code, 0, output)
        self.assertNotIn("BLOCK", output)

    def test_credentials_block_and_synthetic_fixtures_pass(self):
        root = os.path.join(self.tmp, "export")
        self.write(root, "a.py", 'path = r"C:\\Users\\SyntheticUser\\AppData"\n'
                                 "author = 'noreply@github.com'\n")
        self.assertEqual(self.run_gate("--worktree", root)[0], 0)
        self.write(root, "b.txt", "token ghp_" + "A" * 36 + "\n")
        code, output = self.run_gate("--worktree", root)
        self.assertEqual(code, 1)
        self.assertIn("credential:github-token  worktree b.txt:1", output)
        self.assertNotIn("A" * 36, output)

    def test_unknown_personal_email_is_review_not_owner_data(self):
        root = os.path.join(self.tmp, "export")
        self.write(root, "CREDITS.md", "Library by someone@lib-author.dev\n")
        code, output = self.run_gate("--worktree", root)
        self.assertEqual(code, 0)
        self.assertIn("REVIEW  review:email  worktree CREDITS.md:1", output)
        self.assertEqual(self.run_gate("--worktree", root, "--strict")[0], 1)


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class TestHistory(GateTest):
    def git(self, *args, **env):
        full_env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Synthetic Dev", GIT_AUTHOR_EMAIL="dev@example.com",
                        GIT_COMMITTER_NAME="Synthetic Dev", GIT_COMMITTER_EMAIL="dev@example.com")
        full_env.update(env)
        subprocess.run(["git", "-C", self.repo, *args], check=True, capture_output=True, env=full_env)

    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q")

    def commit(self, message, **env):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message, **env)

    def test_scans_removed_blobs_metadata_messages_and_tags(self):
        self.write(self.repo, "old.txt", "phone 555.010.4477\n")
        self.commit("first")
        self.git("rm", "-q", "old.txt")
        self.commit("mention Pat Fictional", GIT_AUTHOR_EMAIL="pat.fictional@example.net")
        self.git("tag", "-a", "v1", "-m", "release", GIT_COMMITTER_NAME="Pat Fictional")
        code, output = self.run_gate("--repo", self.repo)
        self.assertEqual(code, 1)
        lines = output.splitlines()
        self.assertTrue(any("identifier:phone" in l and "old.txt:1" in l for l in lines))
        self.assertTrue(any("identifier:email" in l and " commit:" in l for l in lines))
        self.assertTrue(any("identifier:term" in l and " tag:" in l for l in lines))
        self.assert_never_printed(output)

    def test_private_filename_and_contents_are_both_withheld(self):
        self.write(self.repo, "Pat Fictional notes.txt", "write to pat.fictional@example.net\n")
        self.commit("add notes")
        code, output = self.run_gate("--repo", self.repo)
        self.assertEqual(code, 1)
        self.assertIn(" filename:1", output)
        self.assertIn("identifier:email", output)
        self.assertIn(f"{privacy_gate.WITHHELD}:1", output)
        self.assert_never_printed(output)

    def test_reused_blob_under_private_name_on_another_branch(self):
        self.write(self.repo, "safe.txt", "identical harmless bytes\n")
        self.commit("base")
        self.git("checkout", "-q", "-b", "side")
        self.git("mv", "safe.txt", "Pat Fictional.txt")
        self.commit("rename")
        self.git("checkout", "-q", "-")
        code, output = self.run_gate("--repo", self.repo)
        self.assertEqual(code, 1)
        self.assertTrue(any("identifier:term" in l and " filename:" in l
                            for l in output.splitlines()), output)
        self.assert_never_printed(output)

    def test_private_ref_names_block(self):
        self.write(self.repo, "a.txt", "hello\n")
        self.commit("clean")
        self.git("branch", "pat.fictional-draft")
        code, output = self.run_gate("--repo", self.repo)
        self.assertEqual(code, 1)
        self.assertIn("identifier:term  ref name:1", output)
        self.assert_never_printed(output)

    def test_clean_history_passes(self):
        self.write(self.repo, "a.txt", "hello\n")
        self.commit("clean")
        self.assertEqual(self.run_gate("--repo", self.repo)[0], 0)



class TestAllowList(GateTest):
    """Third-party files in a built installer can be pinned by hash, nothing looser."""

    # Split so this file's own source doesn't trip the history scan.
    LIBRARY = b"docs example: postgres://user:" b"secret@host/db and Pat Fictional\n"

    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "scan")
        self.write(self.root, "lib/vendor.txt", self.LIBRARY)
        self.allow = os.path.join(self.tmp, "allow.txt")

    def write_allow(self, *rows):
        with open(self.allow, "w", encoding="utf-8") as fh:
            fh.write("# pinned third-party hits\n" + "".join(r + "\n" for r in rows))

    def pin(self, category, data=None, path="lib/vendor.txt"):
        digest = hashlib.sha256(self.LIBRARY if data is None else data).hexdigest()
        return f"{digest}  {category}  {path}"

    def test_exact_hash_and_category_are_allowed(self):
        self.write_allow(self.pin("credential:url-password"), self.pin("identifier:term"))
        code, output = self.run_gate("--worktree", self.root, "--allow", self.allow)
        self.assertEqual(code, 0, output)
        self.assertIn("ALLOWED  credential:url-password", output)
        self.assertIn("0 blocking", output)
        self.assert_never_printed(output)

    def test_changed_bytes_block_again(self):
        self.write_allow(self.pin("credential:url-password"), self.pin("identifier:term"))
        self.write(self.root, "lib/vendor.txt", self.LIBRARY + b"more\n")
        code, output = self.run_gate("--worktree", self.root, "--allow", self.allow)
        self.assertEqual(code, 1)
        self.assertIn("no longer matches", output)
        self.assert_never_printed(output)

    def test_only_the_pinned_category_is_allowed(self):
        self.write_allow(self.pin("credential:url-password"))
        code, output = self.run_gate("--worktree", self.root, "--allow", self.allow)
        self.assertEqual(code, 1)
        self.assertIn("BLOCK  identifier:term", output)

    def test_same_bytes_under_another_path_still_block(self):
        self.write_allow(self.pin("credential:url-password"), self.pin("identifier:term"))
        self.write(self.root, "app/copy.txt", self.LIBRARY)
        code, output = self.run_gate("--worktree", self.root, "--allow", self.allow)
        self.assertEqual(code, 1)
        self.assertIn("worktree app/copy.txt", output)

    def test_unused_entry_is_reported(self):
        self.write_allow(self.pin("credential:url-password"), self.pin("identifier:term"),
                         self.pin("credential:private-key", path="lib/gone.txt"))
        code, output = self.run_gate("--worktree", self.root, "--allow", self.allow)
        self.assertEqual(code, 0)
        self.assertIn("matched nothing: lib/gone.txt", output)

    def test_malformed_allow_line_stops(self):
        self.write_allow("not-a-hash  identifier:term  lib/vendor.txt")
        with self.assertRaises(SystemExit) as stop:
            self.run_gate("--worktree", self.root, "--allow", self.allow)
        self.assertIn("line 2", str(stop.exception))

    def test_allow_list_is_refused_for_history_scans(self):
        self.write_allow(self.pin("identifier:term"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_gate("--repo", self.tmp, "--allow", self.allow)


if __name__ == "__main__":
    unittest.main()
