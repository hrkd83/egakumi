"""公開データ検査の境界を、独立した一時Gitリポジトリで確認する。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid


SCANNER = Path(os.environ.get(
    "PUBLIC_DATA_GUARD_SCRIPT",
    str(Path(__file__).resolve().parents[1] / "scripts/check_public_data.py"),
)).resolve()


class PublicDataGuardTests(unittest.TestCase):
    """入力の実値を失敗ログへ再掲せず、CLIの結果を検証する。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="public-guard-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.repository = self.directory / "repository"
        self.repository.mkdir()
        self.environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))
            and key not in {"EGAKUMI_PRIVATE_TERMS_FILE", "GIT_CONFIG_PARAMETERS",
                           "GIT_CONFIG_COUNT", "GIT_DIR", "GIT_WORK_TREE",
                           "GIT_INDEX_FILE"}
        }
        self.environment.update({
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Example developer",
            "GIT_COMMITTER_NAME": "Example developer",
            "GIT_AUTHOR_EMAIL": "developer@example.com",
            "GIT_COMMITTER_EMAIL": "developer@example.com",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Example developer")
        self.git("config", "user.email", "developer@example.com")
        self.git("config", "commit.gpgsign", "false")
        self.policy = self.directory / "trusted-policy.json"
        self.policy_data = {
            "version": 1, "public_uuids": {}, "public_hosts": [],
            "public_emails": [], "public_author_names": ["Example developer"],
            "literal_exceptions": [], "reviewed_binary_assets": [],
        }
        self.save_policy()
        self.write("README.md", "Independent synthetic public fixture.\n")
        self.git("add", "README.md")
        self.git("commit", "--quiet", "-m", "Add synthetic fixture")
        self.initial_commit = self.git("rev-parse", "HEAD").strip().decode()

    def git(self, *arguments: str) -> bytes:
        """Gitの生ログや入力値をアサーションへ渡さない。"""
        result = subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            env=self.environment, capture_output=True, timeout=30, check=False,
        )
        self.assertTrue(result.returncode == 0, "temporary Git setup failed")
        return result.stdout

    def write(self, name: str, content: str | bytes) -> None:
        path = self.repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)

    def save_policy(self) -> None:
        self.policy.write_text(json.dumps(self.policy_data), encoding="utf-8")

    def check(self, *arguments: str, stdin: str | None = None,
              policy: Path | None = None,
              environment: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", str(SCANNER), "--repository",
             str(self.repository), "--policy", str(policy or self.policy),
             *arguments],
            env=environment or self.environment, input=stdin,
            capture_output=True, text=True, timeout=60, check=False,
        )

    def outcome(self, result: subprocess.CompletedProcess, expected: int,
                rule: str | None = None) -> None:
        self.assertTrue(result.returncode == expected, "unexpected guard exit class")
        if rule:
            self.assertTrue(rule in result.stdout, "expected rule was not reported")

    @staticmethod
    def identifier() -> str:
        return str(uuid.uuid4())

    def stage(self, content: str | bytes, name: str = "candidate.txt") -> None:
        self.write(name, content)
        self.git("add", "--", name)

    def test_clean_public_fixture_passes(self) -> None:
        self.outcome(self.check("--staged"), 0)
        self.outcome(self.check("--worktree"), 0)

    def test_safe_index_does_not_read_dirty_worktree(self) -> None:
        self.stage("Synthetic public text.\n")
        self.write("candidate.txt", self.identifier())
        self.outcome(self.check("--staged"), 0)
        self.outcome(self.check("--worktree"), 1, "unknown-uuid")

    def test_unsafe_index_cannot_be_hidden_by_clean_worktree(self) -> None:
        self.stage(self.identifier())
        self.write("candidate.txt", "Synthetic public text.\n")
        self.outcome(self.check("--staged"), 1, "unknown-uuid")
        self.outcome(self.check("--worktree"), 0)

    def test_unknown_uuid_is_blocked_but_obvious_repeat_is_synthetic(self) -> None:
        self.stage(self.identifier())
        self.outcome(self.check("--staged"), 1, "unknown-uuid")
        repeated = "-".join("1" * size for size in (8, 4, 4, 4, 12))
        self.stage(repeated)
        self.outcome(self.check("--staged"), 0)

    def test_quoted_non_uuid_cloud_identifier_is_blocked(self) -> None:
        opaque = "deployment-" + uuid.uuid4().hex
        self.stage(json.dumps({"clientId": opaque}))
        self.outcome(self.check("--staged"), 1, "environment-identifier")

    def test_standard_environment_keys_are_checked_in_shell_files(self) -> None:
        value = "deployment-" + uuid.uuid4().hex
        variants = (
            "export GOOGLE_CLOUD_PROJECT=" + value + "\n",
            '$env:AZURE_CLIENT_ID = "' + value + '"\n',
            "GITHUB_TOKEN: '" + value + "'\n",
        )
        for index, content in enumerate(variants):
            with self.subTest(syntax=index):
                self.stage(content, "candidate.sh")
                self.outcome(self.check("--staged"), 1,
                             "environment-variable-literal")
        runtime_forms = (
            'export GOOGLE_CLOUD_PROJECT="${PROJECT_FROM_PRIVATE_WORKSPACE}"\n',
            'export GOOGLE_CLOUD_PROJECT="$PROJECT_FROM_PRIVATE_WORKSPACE"\n',
            '$env:AZURE_CLIENT_ID = "$env:CLIENT_FROM_PRIVATE_WORKSPACE"\n',
        )
        for index, content in enumerate(runtime_forms):
            with self.subTest(runtime=index):
                self.stage(content, "candidate.sh")
                self.outcome(self.check("--staged"), 0)
        self.stage('export GOOGLE_CLOUD_PROJECT="$PROJECT_ID-' + value + '"\n',
                   "candidate.sh")
        self.outcome(self.check("--staged"), 1, "environment-variable-literal")

    def test_unknown_host_and_email_are_blocked(self) -> None:
        host = "organization-" + uuid.uuid4().hex + ".com"
        for content, rule in (("https://" + host, "nonpublic-host"),
                              ("developer@" + host, "nonpublic-email")):
            with self.subTest(rule=rule):
                self.stage(content)
                self.outcome(self.check("--staged"), 1, rule)

    def test_public_url_host_does_not_approve_every_email_on_that_host(self) -> None:
        host = "github.com"
        approved = "approved-" + uuid.uuid4().hex + "@" + host
        other = "other-" + uuid.uuid4().hex + "@" + host
        self.policy_data["public_hosts"] = [host]
        self.policy_data["public_emails"] = [approved]
        self.save_policy()
        self.stage("https://" + host + "/docs")
        self.outcome(self.check("--staged"), 0)
        self.stage(approved)
        self.outcome(self.check("--staged"), 0)
        self.stage(other)
        self.outcome(self.check("--staged"), 1, "nonpublic-email")

    def test_private_ip_is_blocked_and_documentation_ip_passes(self) -> None:
        private = ".".join(["10", *[str(part) for part in uuid.uuid4().bytes[:3]]])
        self.stage(private)
        self.outcome(self.check("--staged"), 1, "nonpublic-ip")
        self.stage("192.0.2.15\n2001:db8::15\n")
        self.outcome(self.check("--staged"), 0)

    def test_generated_token_shape_is_blocked(self) -> None:
        token = "gh" + "p_" + uuid.uuid4().hex + uuid.uuid4().hex
        self.stage(token)
        self.outcome(self.check("--staged"), 1, "credential-pattern")

    def test_encodings_do_not_hide_unknown_identifier(self) -> None:
        value = self.identifier()
        variants = {
            "utf16": value.encode("utf-16"),
            "base64": base64.b64encode(value.encode()),
            "base64-utf16": base64.b64encode(value.encode("utf-16")),
            "base64-utf16be": base64.b64encode(value.encode("utf-16-be")),
            "percent": "".join("%" + format(ord(char), "02X") for char in value),
        }
        for label, content in variants.items():
            with self.subTest(encoding=label):
                self.stage(content)
                self.outcome(self.check("--staged"), 1, "unknown-uuid")
        # BOMのない直接入力も、未解釈のバイナリーとして通過させない。
        self.stage(value.encode("utf-16-be"))
        self.outcome(self.check("--staged"), 1)

    def test_unknown_binary_is_blocked_and_review_is_exact(self) -> None:
        payload = bytes([0, 255, 1, 2])
        self.stage(payload, "asset.bin")
        self.outcome(self.check("--staged"), 1, "binary-review-required")
        self.policy_data["reviewed_binary_assets"] = [{
            "path": "asset.bin", "sha256": hashlib.sha256(payload).hexdigest(),
            "reason": "Independently generated synthetic test asset",
        }]
        self.save_policy()
        self.outcome(self.check("--staged"), 0)
        self.stage(payload + bytes([3]), "asset.bin")
        self.outcome(self.check("--staged"), 1, "binary-review-required")

    def test_symlink_is_blocked_in_index_and_worktree(self) -> None:
        target = self.directory / "outside.txt"
        target.write_text("Synthetic public data.")
        (self.repository / "linked.txt").symlink_to(target)
        self.git("add", "linked.txt")
        self.outcome(self.check("--staged"), 1, "unsupported-file-mode")
        self.outcome(self.check("--worktree"), 1, "symlink-review-required")

    def test_oversize_content_requires_review(self) -> None:
        self.stage(b"a" * (8 * 1024 * 1024 + 1))
        self.outcome(self.check("--staged"), 1, "oversize-review-required")

    def test_deleted_content_is_still_checked_in_history(self) -> None:
        self.stage(self.identifier())
        self.git("commit", "--quiet", "-m", "Add synthetic rejection fixture")
        self.git("rm", "--quiet", "candidate.txt")
        self.git("commit", "--quiet", "-m", "Remove synthetic rejection fixture")
        self.outcome(self.check("--tracked"), 0)
        self.outcome(self.check("--all-history"), 1, "unknown-uuid")

    def test_pending_commit_message_is_checked(self) -> None:
        message = self.directory / "message.txt"
        message.write_text("Synthetic message " + self.identifier())
        self.outcome(self.check("--commit-message", str(message)), 1, "unknown-uuid")

    def test_branch_name_is_checked(self) -> None:
        self.git("branch", "synthetic-" + self.identifier())
        self.outcome(self.check("--staged"), 1, "unknown-uuid")

    def test_push_can_inspect_an_unreferenced_commit_sha(self) -> None:
        self.stage(self.identifier())
        self.git("commit", "--quiet", "-m", "Synthetic unreferenced fixture")
        revision = self.git("rev-parse", "HEAD").strip().decode()
        self.git("reset", "--hard", self.initial_commit)
        self.outcome(self.check("--all-history"), 0)
        data = "HEAD " + revision + " refs/heads/main " + "0" * 40 + "\n"
        self.outcome(self.check("--pre-push-refs", stdin=data), 1, "unknown-uuid")

    def test_push_destination_name_is_checked(self) -> None:
        destination = "refs/heads/synthetic-" + self.identifier()
        data = "HEAD " + self.initial_commit + " " + destination + " " + "0" * 40 + "\n"
        self.outcome(self.check("--pre-push-refs", stdin=data), 1, "unknown-uuid")

    def test_runtime_checkpoint_does_not_expand_default_public_history(self) -> None:
        self.stage(self.identifier())
        self.git("commit", "--quiet", "-m", "Synthetic runtime checkpoint")
        self.git("rm", "--quiet", "candidate.txt")
        self.git("commit", "--quiet", "-m", "Synthetic checkpoint cleanup")
        checkpoint = self.git("rev-parse", "HEAD").strip().decode()
        self.git("reset", "--hard", self.initial_commit)
        reference = "refs/codex/" + self.identifier() + "/checkpoint"
        self.git("update-ref", reference, checkpoint)
        self.outcome(self.check("--staged"), 0)
        self.outcome(self.check("--all-history"), 0)
        self.outcome(self.check("--revisions", checkpoint), 1, "unknown-uuid")
        data = "HEAD " + checkpoint + " refs/heads/review " + "0" * 40 + "\n"
        self.outcome(self.check("--pre-push-refs", stdin=data), 1, "unknown-uuid")

    def test_explicit_runtime_reference_names_are_still_checked(self) -> None:
        reference = "refs/codex/" + self.identifier() + "/checkpoint"
        self.git("update-ref", reference, self.initial_commit)
        self.outcome(self.check("--staged"), 0)
        self.outcome(self.check("--all-history"), 0)
        self.outcome(self.check("--revisions", reference), 1, "unknown-uuid")
        data = reference + " " + self.initial_commit + " refs/heads/review " + "0" * 40 + "\n"
        self.outcome(self.check("--pre-push-refs", stdin=data), 1, "unknown-uuid")

    def test_shallow_history_fails_closed(self) -> None:
        (self.repository / ".git" / "shallow").write_text(self.initial_commit + "\n")
        self.outcome(self.check("--all-history"), 2, "incomplete-shallow-history")

    def test_missing_policy_fails_closed(self) -> None:
        self.outcome(self.check("--staged", policy=self.directory / "missing.json"),
                     2, "policy-unavailable-or-invalid")

    def test_output_does_not_disclose_input_identity_or_path(self) -> None:
        value = self.identifier()
        name = "evidence-" + value + ".txt"
        self.stage(value, name)
        result = self.check("--staged")
        self.outcome(result, 1, "unknown-uuid")
        output = result.stdout + result.stderr
        self.assertTrue(value not in output and name not in output,
                        "guard disclosed a candidate value or path")

    def test_candidate_policy_cannot_override_explicit_trusted_policy(self) -> None:
        value = self.identifier()
        candidate_policy = dict(self.policy_data)
        candidate_policy["public_uuids"] = {value: "Synthetic attempted override"}
        self.stage(json.dumps(candidate_policy), "scripts/public-data-policy.json")
        self.stage(value)
        self.outcome(self.check("--staged"), 1, "unknown-uuid")

    def test_literal_exception_is_one_value_in_one_path(self) -> None:
        allowed = self.identifier()
        self.policy_data["literal_exceptions"] = [{
            "path": "candidate.txt", "rule": "unknown-uuid", "value": allowed,
            "reason": "Reviewed independent synthetic fixture",
        }]
        self.save_policy()
        self.stage(allowed)
        self.outcome(self.check("--staged"), 0)
        self.stage(allowed, "another.txt")
        self.outcome(self.check("--staged"), 1, "unknown-uuid")
        self.git("rm", "--cached", "another.txt")
        self.stage(self.identifier())
        self.outcome(self.check("--staged"), 1, "unknown-uuid")

    def test_ignored_environment_file_is_not_a_safe_store(self) -> None:
        self.write(".gitignore", ".env*\n")
        self.write(".env.fixture", "Synthetic public content.\n")
        self.outcome(self.check("--worktree"), 1, "sensitive-path")

    def test_private_runtime_roots_are_not_read_by_either_worktree_scan(self) -> None:
        self.write(".gitignore", "/.local/\n/.azure/\n")
        for root in (".local", ".azure"):
            self.write(root + "/fixture/secret.txt", self.identifier())
            self.write(root + "/fixture/evidence.bin", bytes([0, 255, 1, 2]))
        self.outcome(self.check("--worktree"), 0)
        self.outcome(self.check("--worktree-strict"), 0)

    def test_private_runtime_root_symlinks_are_refused_before_exclusion(self) -> None:
        outside = self.directory / "private-fixture"
        outside.mkdir()
        for root in (".local", ".azure"):
            with self.subTest(root=root):
                linked = self.repository / root
                linked.symlink_to(outside, target_is_directory=True)
                self.policy_data["literal_exceptions"] = [{
                    "path": root, "rule": "private-runtime-root-symlink",
                    "value": root, "reason": "Synthetic attempted boundary override",
                }]
                self.save_policy()
                self.outcome(self.check("--worktree"), 1,
                             "private-runtime-root-symlink")
                self.outcome(self.check("--worktree-strict"), 1,
                             "private-runtime-root-symlink")
                linked.unlink()

    def test_private_runtime_exclusion_does_not_cover_nested_names(self) -> None:
        for root in (".local", ".azure"):
            name = "nested/" + root + "/fixture.txt"
            with self.subTest(root=root):
                self.write(name, "Independent synthetic public fixture.\n")
                self.outcome(self.check("--worktree"), 1, "sensitive-path")
                self.outcome(self.check("--worktree-strict"), 1, "sensitive-path")
                (self.repository / name).unlink()

    def test_forced_private_tracking_is_refused_without_reading_or_exemptions(self) -> None:
        self.write(".gitignore", "/.local/\n/.azure/\n")
        content = b"Independent synthetic public fixture.\n"
        names = [root + "/fixture.txt" for root in (".local", ".azure")]
        for name in names:
            self.write(name, content)
            self.git("add", "-f", "--", name)
        self.policy_data["literal_exceptions"] = [{
            "path": name, "rule": "private-runtime-path-tracked", "value": name,
            "reason": "Synthetic attempted boundary override",
        } for name in names]
        self.policy_data["reviewed_binary_assets"] = [{
            "path": name, "sha256": hashlib.sha256(content).hexdigest(),
            "reason": "Independently generated synthetic review fixture",
        } for name in names]
        self.save_policy()
        for mode in ("--staged", "--tracked", "--worktree", "--worktree-strict"):
            with self.subTest(mode=mode):
                self.outcome(self.check(mode), 1, "private-runtime-path-tracked")
        self.git("commit", "--quiet", "-m", "Add synthetic private tracking fixture")
        self.outcome(self.check("--all-history"), 1, "private-runtime-path-tracked")

    def test_deleted_private_paths_remain_blocked_in_history_and_push(self) -> None:
        names = [root + "/fixture.txt" for root in (".local", ".azure")]
        for name in names:
            self.stage("Independent synthetic public fixture.\n", name)
        self.git("commit", "--quiet", "-m", "Add synthetic private history fixture")
        self.git("rm", "--quiet", *names)
        self.git("commit", "--quiet", "-m", "Remove synthetic private history fixture")
        self.outcome(self.check("--staged"), 0)
        self.outcome(self.check("--tracked"), 0)
        self.outcome(self.check("--all-history"), 1, "private-runtime-path-tracked")
        self.outcome(self.check("--revisions", "HEAD"), 1,
                     "private-runtime-path-tracked")
        revision = self.git("rev-parse", "HEAD").strip().decode()
        data = "HEAD " + revision + " refs/heads/review " + "0" * 40 + "\n"
        self.outcome(self.check("--pre-push-refs", stdin=data), 1,
                     "private-runtime-path-tracked")

    def test_misplaced_runtime_paths_are_forbidden_even_if_locally_ignored(self) -> None:
        names = (".state/fixture.txt", "secrets/fixture.txt",
                 "params/fixture.local.json", "infra/main.parameters.json")
        self.write(".gitignore", ".state/\nsecrets/\nparams/*.local.json\n"
                   "infra/main.parameters.json\n")
        for name in names:
            self.write(name, "Independent synthetic public fixture.\n")
        self.outcome(self.check("--worktree"), 1, "sensitive-path")
        self.outcome(self.check("--worktree-strict"), 1, "sensitive-path")

    def test_repository_ignore_only_hides_private_roots_and_current_caches(self) -> None:
        source = Path(__file__).resolve().parents[1] / ".gitignore"
        self.write(".gitignore", source.read_text(encoding="utf-8"))
        allowed = {
            ".local/example/params.local.json", ".azure/example/.env",
            "__pycache__/fixture.pyc", ".venv/fixture", ".venv-example/fixture",
            "apps/portal/node_modules/fixture", "apps/portal/.next/fixture",
            "docs/study/fixture", ".DS_Store",
        }
        visible = {
            ".state/result.json", "secrets/fixture.txt", "params/example.local.json",
            "infra/main.parameters.json", "infra/main.json", "infra/bicepconfig.json",
            "nested/.local/fixture", "nested/.azure/fixture", ".tools/fixture",
            "artifacts/result.json", "outputs/result.json", "Archives/fixture",
            "fixture.pem", "fixture.key", "fixture.pfx", "fixture.cer", "fixture.token",
            "fixture.log", ".env", ".env.local", "apps/portal/.env.local",
        }
        inspected = subprocess.run(
            ["git", "-C", str(self.repository), "check-ignore", "--no-index", "--stdin", "-z"],
            input=b"\0".join(name.encode() for name in sorted(allowed | visible)) + b"\0",
            env=self.environment, capture_output=True, timeout=30, check=False,
        )
        self.assertTrue(inspected.returncode == 0, "temporary ignore inspection failed")
        self.assertEqual({os.fsdecode(name) for name in inspected.stdout.split(b"\0") if name}, allowed)

    def test_dependency_exclusion_does_not_exempt_index_or_strict_scan(self) -> None:
        name = "apps/portal/node_modules/synthetic/fixture.txt"
        self.stage(self.identifier(), name)
        self.outcome(self.check("--worktree"), 0)
        self.outcome(self.check("--staged"), 1, "unknown-uuid")
        self.outcome(self.check("--worktree-strict"), 1, "unknown-uuid")

    def test_local_study_media_is_excluded_only_from_normal_worktree(self) -> None:
        self.write("docs/study/output/fixture.bin", bytes([0, 255, 1, 2]))
        self.outcome(self.check("--worktree"), 0)
        self.outcome(self.check("--worktree-strict"), 1, "binary-review-required")
        self.write("outside-study.txt", self.identifier())
        self.outcome(self.check("--worktree"), 1, "unknown-uuid")

    def test_local_study_root_symlink_is_not_exempt(self) -> None:
        outside = self.directory / "outside-study"
        outside.mkdir()
        (self.repository / "docs").mkdir()
        (self.repository / "docs/study").symlink_to(outside, target_is_directory=True)
        self.outcome(self.check("--worktree"), 1, "symlink-review-required")

    def test_forced_study_tracking_is_refused_even_with_reviewed_hash(self) -> None:
        name = "docs/study/fixture.md"
        content = b"Independent synthetic study fixture.\n"
        self.write(".gitignore", "docs/study/\n")
        self.write(name, content)
        self.git("add", "-f", "--", name)
        self.policy_data["reviewed_binary_assets"] = [{
            "path": name, "sha256": hashlib.sha256(content).hexdigest(),
            "reason": "Independently generated synthetic review fixture",
        }]
        self.save_policy()
        self.outcome(self.check("--staged"), 1, "local-only-path-tracked")
        self.outcome(self.check("--tracked"), 1, "local-only-path-tracked")
        self.git("commit", "--quiet", "-m", "Add synthetic study fixture")
        self.outcome(self.check("--all-history"), 1, "local-only-path-tracked")

    def test_removed_study_history_is_readable_but_old_push_tip_is_refused(self) -> None:
        name = "docs/study/fixture.md"
        self.stage("Independent synthetic study fixture.\n", name)
        self.git("commit", "--quiet", "-m", "Add synthetic legacy study fixture")
        previous = self.git("rev-parse", "HEAD").strip().decode()
        self.git("rm", "--quiet", name)
        self.git("commit", "--quiet", "-m", "Stop tracking synthetic study fixture")
        self.git("update-ref", "refs/remotes/origin/legacy-study", previous)
        self.git("tag", "legacy-study", previous)
        self.outcome(self.check("--staged"), 0)
        self.outcome(self.check("--tracked"), 0)
        self.outcome(self.check("--all-history"), 0)
        self.outcome(self.check("--revisions", previous), 1, "local-only-path-tracked")
        data = "HEAD " + previous + " refs/heads/review " + "0" * 40 + "\n"
        self.outcome(self.check("--pre-push-refs", stdin=data),
                     1, "local-only-path-tracked")

    def test_push_checks_dangling_annotated_tag_message(self) -> None:
        self.git("tag", "-a", "synthetic-review", "-m",
                 "Synthetic annotation " + self.identifier())
        tag_object = self.git("rev-parse", "refs/tags/synthetic-review").strip().decode()
        self.git("tag", "--delete", "synthetic-review")
        self.outcome(self.check("--all-history"), 0)
        data = "HEAD " + tag_object + " refs/tags/review " + "0" * 40 + "\n"
        self.outcome(self.check("--pre-push-refs", stdin=data), 1, "unknown-uuid")

    def test_nested_base64_cannot_escape_the_depth_limit(self) -> None:
        payload = self.identifier().encode()
        for _ in range(6):
            payload = base64.b64encode(payload)
        self.stage(payload)
        self.outcome(self.check("--staged"), 1, "encoding-depth-review-required")

    def test_unicode_hostname_is_not_a_host_parser_bypass(self) -> None:
        host = "企業" + uuid.uuid4().hex + ".com"
        self.stage("https://" + host + "/")
        self.outcome(self.check("--staged"), 1, "nonpublic-host")

    def test_unquoted_yaml_identifier_is_not_a_quote_parser_bypass(self) -> None:
        value = "deployment-" + uuid.uuid4().hex
        setting = "client" + "Id"
        self.stage(setting + ": " + value + "\n", "candidate.yaml")
        self.outcome(self.check("--staged"), 1, "unquoted-sensitive-setting")

    def test_unapproved_data_uri_requires_review(self) -> None:
        payload = base64.b64encode(bytes([0, 255, 1, 2])).decode()
        self.stage("data:application/octet-stream;base64," + payload)
        self.outcome(self.check("--staged"), 1, "embedded-data-review-required")

    def test_author_and_private_terms_are_separate_approval_boundaries(self) -> None:
        environment = self.environment.copy()
        author = "Synthetic contributor " + uuid.uuid4().hex
        environment.update({"GIT_AUTHOR_NAME": author, "GIT_COMMITTER_NAME": author})
        self.outcome(self.check("--staged", environment=environment),
                     1, "unapproved-author-name")

        term = "organizationlabel" + uuid.uuid4().hex
        terms_file = self.directory / "private-terms.txt"
        terms_file.write_text(term + "\n")
        self.stage(term)
        environment = {**self.environment,
                       "EGAKUMI_PRIVATE_TERMS_FILE": str(terms_file)}
        self.outcome(self.check("--staged", environment=environment), 1, "private-term")

        local_terms = self.repository / "private-terms.txt"
        local_terms.write_text(term + "\n")
        environment["EGAKUMI_PRIVATE_TERMS_FILE"] = str(local_terms)
        self.outcome(self.check("--staged", environment=environment),
                     2, "private-terms-must-stay-outside-checkout")


if __name__ == "__main__":
    unittest.main()
