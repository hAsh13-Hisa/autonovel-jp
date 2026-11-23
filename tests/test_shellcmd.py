"""shellcmd（shell=True を使わない実行）のテスト。LLM も外部コマンドも呼ばない。"""
import subprocess
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import shellcmd  # noqa: E402


class SplitCommandTest(unittest.TestCase):
    def test_simple_string(self):
        argv, env = shellcmd.split_command("git status --short")
        self.assertEqual(argv, ["git", "status", "--short"])
        self.assertEqual(env, {})

    def test_quoted_argument_with_space(self):
        argv, _ = shellcmd.split_command('prog --band "3000 99999" chapters/ch_01.md')
        self.assertEqual(argv, ["prog", "--band", "3000 99999", "chapters/ch_01.md"])

    def test_env_prefix_is_split_out(self):
        argv, env = shellcmd.split_command('AUTONOVEL_X=1 AUTONOVEL_Y="a b" prog --flag')
        self.assertEqual(argv, ["prog", "--flag"])
        self.assertEqual(env, {"AUTONOVEL_X": "1", "AUTONOVEL_Y": "a b"})

    def test_metacharacter_in_quotes_is_allowed(self):
        # grep の交互パターン（'He did not\|He had not'）は引用符の中なので通す
        argv, _ = shellcmd.split_command("grep -c 'He did not\\|He had not' chapters/ch_01.md")
        self.assertEqual(argv[0], "grep")
        self.assertIn("He did not\\|He had not", argv)

    def test_pipe_is_rejected(self):
        with self.assertRaises(ValueError):
            shellcmd.split_command("cat file | wc -l")

    def test_redirect_is_rejected(self):
        with self.assertRaises(ValueError):
            shellcmd.split_command("git show HEAD:outline.md > /tmp/x.md")

    def test_and_chain_is_rejected(self):
        with self.assertRaises(ValueError):
            shellcmd.split_command("git add -A && git commit -m x")

    def test_command_substitution_is_rejected(self):
        with self.assertRaises(ValueError):
            shellcmd.split_command("echo $(whoami)")

    def test_list_input_passes_through(self):
        argv, env = shellcmd.split_command(["git", "checkout", "--", "chapters/ch_01.md"])
        self.assertEqual(argv, ["git", "checkout", "--", "chapters/ch_01.md"])
        self.assertEqual(env, {})

    def test_empty_command_is_rejected(self):
        with self.assertRaises(ValueError):
            shellcmd.split_command("   ")


class RunTest(unittest.TestCase):
    def test_runs_without_shell(self):
        res = shellcmd.run([sys.executable, "-c", "print('ok')"])
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "ok")

    def test_env_prefix_reaches_child(self):
        res = shellcmd.run('AUTONOVEL_TEST_VAR=hello ' + sys.executable +
                           ' -c "import os;print(os.environ.get(\'AUTONOVEL_TEST_VAR\'))"')
        self.assertEqual(res.stdout.strip(), "hello")

    def test_returns_completed_process(self):
        res = shellcmd.run([sys.executable, "-c", "import sys;sys.exit(3)"])
        self.assertIsInstance(res, subprocess.CompletedProcess)
        self.assertEqual(res.returncode, 3)


if __name__ == "__main__":
    unittest.main()
