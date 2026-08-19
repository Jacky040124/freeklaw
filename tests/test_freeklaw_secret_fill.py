from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "lib" / "freeklaw_secret_fill.py"
SECRET = "secret-value-that-must-never-leak"
SPEC = importlib.util.spec_from_file_location("freeklaw_secret_fill_tested", HELPER)
assert SPEC is not None and SPEC.loader is not None
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


class SecretFillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.capture_dir = self.root / "capture"
        self.capture_dir.mkdir()
        self.bridge_root = self.root / "runtime" / "secret-bridge"
        self.vault_root = self.root / "runtime" / "npm"
        self.ego_root = self.root / "Applications" / "ego lite.app"
        self.vault_real = self.vault_root / "lib" / "agent-vault"
        self.ego_real = self.ego_root / "Contents" / "Helpers" / "ego-browser"
        self._write_executable(self.vault_real, self._vault_source())
        self._write_executable(self.ego_real, self._ego_source())

        launchers = self.root / "launchers"
        launchers.mkdir()
        self.vault_launcher = launchers / "agent-vault"
        self.ego_launcher = launchers / "ego-browser"
        self.vault_launcher.symlink_to(self.vault_real)
        self.ego_launcher.symlink_to(self.ego_real)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_executable(self, path: Path, source: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"#!{sys.executable}\n{textwrap.dedent(source)}",
            encoding="utf-8",
        )
        path.chmod(0o755)

    @staticmethod
    def _vault_source() -> str:
        return """\
        import json
        import os
        import pathlib
        import stat
        import sys

        capture = pathlib.Path(os.environ["CAPTURE_DIR"])
        command = sys.argv[1]
        if command == "has":
            raise SystemExit(int(os.environ.get("VAULT_HAS_EXIT", "5")))
        if command == "set":
            (capture / "set.json").write_text(json.dumps({
                "argv": sys.argv[1:],
                "password": sys.stdin.read(),
            }))
            raise SystemExit(int(os.environ.get("VAULT_SET_EXIT", "0")))
        target = pathlib.Path(sys.argv[2])
        (capture / "vault.json").write_text(json.dumps({
            "argv0": sys.argv[0],
            "argv": sys.argv[1:],
            "path": str(target),
            "file_mode": stat.S_IMODE(target.stat().st_mode),
            "directory_mode": stat.S_IMODE(target.parent.stat().st_mode),
            "bridge_mode": stat.S_IMODE(target.parent.parent.stat().st_mode),
            "pid": os.getpid(),
            "pgid": os.getpgrp(),
        }))
        code = int(os.environ.get("VAULT_EXIT", "0"))
        if code:
            print(os.environ["TEST_SECRET"])
            print(os.environ["TEST_SECRET"], file=sys.stderr)
            raise SystemExit(code)
        if os.environ.get("VAULT_REPLACE_WITH_SYMLINK"):
            target.unlink()
            target.symlink_to(os.environ["PROTECTED_FILE"])
            raise SystemExit(0)
        stored = capture / "set.json"
        if stored.exists():
            target.write_text(
                json.loads(stored.read_text(encoding="utf-8"))["password"],
                encoding="utf-8",
            )
            raise SystemExit(0)
        target.write_text(os.environ["TEST_SECRET"], encoding="utf-8")
        print(os.environ["TEST_SECRET"])
        print(os.environ["TEST_SECRET"], file=sys.stderr)
        """

    @staticmethod
    def _ego_source() -> str:
        return """\
        import json
        import os
        import pathlib
        import re
        import signal
        import subprocess
        import sys
        import time

        capture = pathlib.Path(os.environ["CAPTURE_DIR"])
        source = sys.stdin.read()
        def literal(name):
            match = re.search(rf"^const {name} = (.+);$", source, re.MULTILINE)
            if not match:
                raise RuntimeError(name)
            return json.loads(match.group(1))
        path = pathlib.Path(literal("secretPath"))
        secret = path.read_text(encoding="utf-8")
        (capture / "ego.json").write_text(json.dumps({
            "argv0": sys.argv[0],
            "argv": sys.argv[1:],
            "source": source,
            "path": str(path),
            "task_space": literal("requestedTaskSpace"),
            "locator": literal("requestedLocator"),
            "confirm_locator": literal("requestedConfirmLocator"),
            "secret": secret,
            "mode": path.stat().st_mode & 0o777,
            "secret_was_read": secret == os.environ["TEST_SECRET"],
            "pid": os.getpid(),
            "pgid": os.getpgrp(),
        }))
        if os.environ.get("SPAWN_DESCENDANT"):
            marker = capture / "descendant-terminated"
            code = (
                "import pathlib, signal, sys, time\\n"
                "marker = pathlib.Path(sys.argv[1])\\n"
                "def stop(*_args):\\n"
                "    marker.write_text('terminated')\\n"
                "    raise SystemExit(0)\\n"
                "signal.signal(signal.SIGTERM, stop)\\n"
                "marker.with_name('descendant-ready').write_text("
                "str(__import__('os').getpid()))\\n"
                "time.sleep(60)\\n"
            )
            subprocess.Popen([sys.executable, "-c", code, str(marker)])
            ready = capture / "descendant-ready"
            for _ in range(100):
                if ready.exists():
                    break
                time.sleep(0.01)
            os.kill(os.getppid(), signal.SIGTERM)
            if os.environ.get("DOUBLE_SIGNAL"):
                time.sleep(0.02)
                os.kill(os.getppid(), signal.SIGTERM)
            time.sleep(60)
        print(secret)
        print(secret, file=sys.stderr)
        raise SystemExit(int(os.environ.get("EGO_EXIT", "0")))
        """

    def _environment(self, **overrides: str) -> dict[str, str]:
        env = {"CAPTURE_DIR": str(self.capture_dir), "TEST_SECRET": SECRET}
        env.update(overrides)
        return env

    def _run(
        self,
        *extra: str,
        environment: dict[str, str] | None = None,
        bridge_overrides: dict[str, object] | None = None,
    ) -> types.SimpleNamespace:
        arguments = [
                "--task-space",
                "existing-space",
                "--locator",
                "input[name=password]",
                "--vault-key",
                "ats-password",
                *extra,
        ]
        constants: dict[str, object] = {
            "_VAULT_LAUNCHER": self.vault_launcher,
            "_VAULT_ROOT": self.vault_root,
            "_EGO_LAUNCHER": self.ego_launcher,
            "_EGO_ROOTS": (self.ego_root,),
            "_BRIDGE_ROOT": self.bridge_root,
        }
        constants.update(bridge_overrides or {})
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.multiple(BRIDGE, **constants),
            mock.patch.dict(os.environ, environment or self._environment()),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                returncode = BRIDGE.main(arguments)
            except SystemExit as error:
                returncode = int(error.code)
        return types.SimpleNamespace(
            returncode=returncode,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
        )

    def _capture(self, name: str) -> dict[str, object]:
        return json.loads((self.capture_dir / name).read_text(encoding="utf-8"))

    def test_success_uses_resolved_binaries_private_files_and_cleans_up(self) -> None:
        result = self._run()

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '{"ok":true}\n')
        self.assertEqual(result.stderr, "")
        vault = self._capture("vault.json")
        ego = self._capture("ego.json")
        self.assertEqual(Path(str(vault["argv0"])), self.vault_real.resolve())
        self.assertEqual(Path(str(ego["argv0"])), self.ego_real.resolve())
        self.assertEqual(vault["pid"], vault["pgid"])
        self.assertEqual(ego["pid"], ego["pgid"])
        self.assertEqual(vault["file_mode"], 0o600)
        self.assertEqual(vault["directory_mode"], 0o700)
        self.assertEqual(vault["bridge_mode"], 0o700)
        self.assertEqual(ego["mode"], 0o600)
        self.assertTrue(ego["secret_was_read"])
        self.assertNotIn(SECRET, json.dumps(vault))
        self.assertNotIn(SECRET, str(ego["source"]))
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertFalse(Path(str(ego["path"])).exists())
        self.assertEqual(list(self.bridge_root.iterdir()), [])

    def test_generate_stores_strong_password_and_fills_both_locators(self) -> None:
        result = self._run(
            "--confirm-locator",
            "input[name=confirm]",
            "--generate",
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '{"ok":true}\n')
        stored = self._capture("set.json")
        self.assertEqual(
            stored["argv"],
            ["set", "ats-password", "--stdin", "--desc", "Created by Freeklaw"],
        )
        password = str(stored["password"])
        self.assertEqual(len(password), 24)
        self.assertTrue(any(character.islower() for character in password))
        self.assertTrue(any(character.isupper() for character in password))
        self.assertTrue(any(character.isdigit() for character in password))
        self.assertTrue(any(character in "!#$%*+-_" for character in password))
        ego = self._capture("ego.json")
        self.assertEqual(ego["secret"], password)
        self.assertEqual(ego["confirm_locator"], "input[name=confirm]")
        self.assertIn(
            "await fillInput(requestedConfirmLocator, secret)", str(ego["source"])
        )
        self.assertNotIn(password, str(ego["source"]))
        self.assertNotIn(password, result.stdout + result.stderr)

    def test_generate_refuses_to_overwrite_an_existing_vault_key(self) -> None:
        result = self._run(
            "--generate",
            environment=self._environment(VAULT_HAS_EXIT="0"),
        )

        self.assertEqual(result.returncode, 65)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            '{"ok":false,"stage":"vault-key-exists","exit_code":65}\n',
        )
        self.assertFalse((self.capture_dir / "set.json").exists())
        self.assertFalse((self.capture_dir / "ego.json").exists())

    def test_fill_without_confirm_locator_passes_null(self) -> None:
        result = self._run()

        self.assertEqual(result.returncode, 0)
        ego = self._capture("ego.json")
        self.assertIsNone(ego["confirm_locator"])

    def test_path_shadowing_is_ignored(self) -> None:
        shadow = self.root / "shadow"
        marker = self.capture_dir / "path-shadow-ran"
        self._write_executable(
            shadow / "agent-vault",
            f"import pathlib\npathlib.Path({str(marker)!r}).write_text('bad')\n",
        )
        env = self._environment(PATH=str(shadow))

        result = self._run(environment=env)

        self.assertEqual(result.returncode, 0)
        self.assertFalse(marker.exists())

    def test_task_space_and_locator_are_json_encoded(self) -> None:
        task_space = 'space"; throw new Error("injected") //'
        locator = 'input[name="pass\\word"]\n; process.exit(9) //'
        result = self._run(
            "--task-space",
            task_space,
            "--locator",
            locator,
        )

        self.assertEqual(result.returncode, 0)
        ego = self._capture("ego.json")
        self.assertEqual(ego["task_space"], task_space)
        self.assertEqual(ego["locator"], locator)
        self.assertIn("await fillInput(requestedLocator, secret)", str(ego["source"]))
        self.assertIn("await claimTaskSpace(existingId)", str(ego["source"]))
        self.assertNotIn("useOrCreateTaskSpace", str(ego["source"]))

    def test_vault_failure_is_suppressed_propagated_and_cleaned_up(self) -> None:
        result = self._run(environment=self._environment(VAULT_EXIT="23"))

        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            '{"ok":false,"stage":"agent-vault","exit_code":23}\n',
        )
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        vault = self._capture("vault.json")
        self.assertFalse(Path(str(vault["path"])).exists())
        self.assertFalse((self.capture_dir / "ego.json").exists())

    def test_ego_failure_is_suppressed_propagated_and_cleaned_up(self) -> None:
        result = self._run(environment=self._environment(EGO_EXIT="17"))

        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            '{"ok":false,"stage":"ego-browser","exit_code":17}\n',
        )
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        ego = self._capture("ego.json")
        self.assertFalse(Path(str(ego["path"])).exists())

    def test_invalid_vault_key_is_refused_before_subprocesses(self) -> None:
        result = self._run("--vault-key", "Bad/key")

        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            '{"ok":false,"stage":"arguments","exit_code":64}\n',
        )
        self.assertFalse((self.capture_dir / "vault.json").exists())
        self.assertFalse((self.capture_dir / "ego.json").exists())

    def test_help_is_fixed_json_only(self) -> None:
        result = self._run("--help")

        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            '{"ok":false,"stage":"arguments","exit_code":64}\n',
        )

    def test_replaced_secret_file_is_rejected_without_following_symlink(self) -> None:
        protected = self.root / "protected"
        protected.write_text("do-not-change", encoding="utf-8")
        protected.chmod(0o644)
        env = self._environment(
            VAULT_REPLACE_WITH_SYMLINK="1",
            PROTECTED_FILE=str(protected),
        )

        result = self._run(environment=env)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(
            result.stderr,
            '{"ok":false,"stage":"agent-vault","exit_code":70}\n',
        )
        self.assertEqual(protected.read_text(encoding="utf-8"), "do-not-change")
        self.assertEqual(stat.S_IMODE(protected.stat().st_mode), 0o644)
        self.assertFalse((self.capture_dir / "ego.json").exists())

    def test_executable_outside_allowed_root_is_rejected_without_path_leak(self) -> None:
        secret_path_component = self.root / SECRET / "agent-vault"
        self._write_executable(secret_path_component, "raise SystemExit(0)\n")
        result = self._run(
            bridge_overrides={"_VAULT_LAUNCHER": secret_path_component}
        )

        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            '{"ok":false,"stage":"configuration","exit_code":64}\n',
        )
        self.assertNotIn(SECRET, result.stderr)
        self.assertFalse((self.capture_dir / "vault.json").exists())

    def test_symlink_escaping_allowed_root_is_rejected(self) -> None:
        outside = self.root / "outside" / "agent-vault"
        self._write_executable(outside, "raise SystemExit(0)\n")
        escaping = self.vault_root / "bin" / "escaping-agent-vault"
        escaping.parent.mkdir(parents=True, exist_ok=True)
        escaping.symlink_to(outside)
        result = self._run(bridge_overrides={"_VAULT_LAUNCHER": escaping})

        self.assertEqual(result.returncode, 64)
        self.assertFalse((self.capture_dir / "vault.json").exists())

    def test_stale_run_directories_and_files_are_removed_safely(self) -> None:
        stale_directory = self.bridge_root / "run-stale"
        stale_directory.mkdir(parents=True)
        (stale_directory / "secret").write_text(SECRET, encoding="utf-8")
        stale_file = self.bridge_root / "run-stale-file"
        stale_file.write_text(SECRET, encoding="utf-8")
        old = time.time() - 120
        os.utime(stale_directory, (old, old))
        os.utime(stale_file, (old, old))
        preserved = self.bridge_root / "not-owned"
        preserved.write_text("keep", encoding="utf-8")

        result = self._run()

        self.assertEqual(result.returncode, 0)
        self.assertFalse(stale_directory.exists())
        self.assertFalse(stale_file.exists())
        self.assertEqual(preserved.read_text(encoding="utf-8"), "keep")

    def test_run_directory_is_removed_if_lock_creation_fails(self) -> None:
        real_open = os.open

        def fail_lock(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            **kwargs: object,
        ) -> int:
            if Path(path).name == ".lock":
                raise OSError
            return real_open(path, flags, mode, **kwargs)

        with (
            mock.patch.object(BRIDGE.os, "open", side_effect=fail_lock),
            self.assertRaises(OSError),
        ):
            BRIDGE._create_run_directory(self.bridge_root)

        self.assertEqual(list(self.bridge_root.iterdir()), [])

    def test_signal_terminates_descendant_process_group_and_cleans_up(self) -> None:
        result = self._run(
            environment=self._environment(SPAWN_DESCENDANT="1", DOUBLE_SIGNAL="1")
        )

        self.assertEqual(result.returncode, 143)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            '{"ok":false,"stage":"ego-browser","exit_code":143}\n',
        )
        deadline = time.monotonic() + 1.0
        marker = self.capture_dir / "descendant-terminated"
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists())
        ego = self._capture("ego.json")
        self.assertFalse(Path(str(ego["path"])).exists())
        self.assertEqual(
            [path.name for path in self.bridge_root.iterdir() if path.name != "not-owned"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
