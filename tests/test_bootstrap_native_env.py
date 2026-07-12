import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap_native_env.sh"


class BootstrapNativeEnvTests(unittest.TestCase):
    def _write_fake_python(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import json
                import os
                import shutil
                import sys
                from pathlib import Path

                args = sys.argv[1:]
                log_path = Path(os.environ["FAKE_LOG"])
                with log_path.open("a", encoding="utf-8") as log:
                    log.write(json.dumps({{
                        "program": Path(sys.argv[0]).name,
                        "args": args,
                        "cwd": os.getcwd(),
                        "python_optimize": os.environ.get("PYTHONOPTIMIZE"),
                    }}) + "\\n")

                is_environment_python = Path(sys.argv[0]).name == "python"
                if args[:1] == ["-c"]:
                    if is_environment_python:
                        code = args[1]
                        if "assert platform.machine()" in code and os.environ.get("PYTHONOPTIMIZE"):
                            raise SystemExit(0)
                        print(os.environ.get("FAKE_ENV_MACHINE", "arm64"))
                    else:
                        print(os.environ.get("FAKE_INITIAL_MACHINE", "arm64"))
                    raise SystemExit(0)

                if args[:2] == ["-m", "venv"]:
                    environment_python = Path(args[2]) / "bin" / "python"
                    environment_python.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(__file__, environment_python)
                    raise SystemExit(0)

                if args[:2] == ["-m", "pip"]:
                    if "-r" in args:
                        requirements = Path(args[args.index("-r") + 1])
                        if not requirements.is_file():
                            print(f"requirements file not found: {{requirements}}", file=sys.stderr)
                            raise SystemExit(12)
                    raise SystemExit(0)

                print(f"unexpected fake Python arguments: {{args}}", file=sys.stderr)
                raise SystemExit(13)
                """
            ),
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    def _run_bootstrap(
        self,
        script: Path,
        python_bin: Path,
        cwd: Path,
        log_path: Path,
        env_dir: Path | None = None,
        **environment_overrides: str,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHON_BIN": str(python_bin),
                "FAKE_LOG": str(log_path),
                **environment_overrides,
            }
        )
        if env_dir is not None:
            environment["ENV_DIR"] = str(env_dir)
        else:
            environment.pop("ENV_DIR", None)
        return subprocess.run(
            [str(script)],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def _read_log(self, path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_python_path_with_spaces_is_invoked_as_one_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            temporary_root = Path(tmp)
            python_bin = self._write_fake_python(temporary_root / "python tools" / "fake python")
            env_dir = temporary_root / "environment"
            log_path = temporary_root / "calls.jsonl"

            result = self._run_bootstrap(BOOTSTRAP, python_bin, ROOT, log_path, env_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(env_dir.joinpath("bin", "python").is_file())
            self.assertEqual(self._read_log(log_path)[0]["program"], "fake python")

    def test_existing_environment_is_refused_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            temporary_root = Path(tmp)
            python_bin = self._write_fake_python(temporary_root / "fake-python")
            env_dir = temporary_root / "existing-environment"
            env_dir.mkdir()
            sentinel = env_dir / "preserve-me.txt"
            sentinel.write_text("original", encoding="utf-8")
            log_path = temporary_root / "calls.jsonl"

            result = self._run_bootstrap(BOOTSTRAP, python_bin, ROOT, log_path, env_dir)

            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("already exists", result.stderr)
            self.assertIn("remove or rename", result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "original")
            calls = self._read_log(log_path)
            self.assertFalse(any(call["args"][:2] == ["-m", "venv"] for call in calls))

    def test_optimized_mode_cannot_disable_final_architecture_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            temporary_root = Path(tmp)
            python_bin = self._write_fake_python(temporary_root / "fake-python")
            env_dir = temporary_root / "environment"
            log_path = temporary_root / "calls.jsonl"

            result = self._run_bootstrap(
                BOOTSTRAP,
                python_bin,
                ROOT,
                log_path,
                env_dir,
                FAKE_ENV_MACHINE="x86_64",
                PYTHONOPTIMIZE="1",
            )

            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertIn("created environment reports x86_64", result.stderr)
            final_call = self._read_log(log_path)[-1]
            self.assertEqual(final_call["program"], "python")
            self.assertEqual(final_call["python_optimize"], "1")

    def test_default_environment_and_requirements_are_anchored_to_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            temporary_root = Path(tmp)
            repository = temporary_root / "repository"
            scripts_dir = repository / "scripts"
            scripts_dir.mkdir(parents=True)
            script = scripts_dir / BOOTSTRAP.name
            shutil.copy2(BOOTSTRAP, script)
            requirements = repository / "requirements-dev.txt"
            requirements.write_text("pytest>=9.0,<10.0\n", encoding="utf-8")
            foreign_cwd = temporary_root / "foreign-cwd"
            foreign_cwd.mkdir()
            python_bin = self._write_fake_python(temporary_root / "fake-python")
            log_path = temporary_root / "calls.jsonl"

            result = self._run_bootstrap(script, python_bin, foreign_cwd, log_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(repository.joinpath(".venv-native", "bin", "python").is_file())
            self.assertFalse(foreign_cwd.joinpath(".venv-native").exists())
            requirements_calls = [call for call in self._read_log(log_path) if "-r" in call["args"]]
            self.assertEqual(requirements_calls[0]["args"][-1], str(requirements))


if __name__ == "__main__":
    unittest.main()
