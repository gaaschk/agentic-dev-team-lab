"""Agents share a run workspace, and nothing reaches outside it."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch

from team_agents import sandbox
from team_agents.profiles import load_profiles
from team_agents.workbench import Workbench


def call(bench, tool, path="", content="", command=""):
    return bench.run_tool({"tool": tool, "path": path, "content": content, "command": command})


class Workspaces(TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name).resolve()
        for name in ("architect", "reviewer", "run"):
            (self.root / name).mkdir()
        (self.root / "outside.txt").write_text("secret")
        (self.root / "reviewer" / "x").write_text("another agent's memory")

    def bench(self, agent, shell=False):
        return Workbench(self.root / agent, shell=shell, shared=self.root / "run")


class SharedWorkspaceTests(Workspaces):
    def test_agents_collaborate_through_the_shared_workspace(self):
        architect, reviewer = self.bench("architect"), self.bench("reviewer")
        self.assertTrue(call(architect, "write_file", "run/notes/api.md", "use sqlite")[0])
        self.assertTrue(call(architect, "write_file", "private.md", "mine")[0])
        self.assertIn("run/notes/api.md", call(reviewer, "list_files")[1])
        self.assertNotIn("private.md", call(reviewer, "list_files")[1])
        self.assertEqual(call(reviewer, "read_file", "run/notes/api.md")[1], "use sqlite")
        self.assertEqual(call(reviewer, "search_files", content="sqlite")[1],
                         "run/notes/api.md:1: use sqlite")

    def test_file_tools_refuse_to_leave_either_workspace(self):
        bench = self.bench("architect")
        (self.root / "run" / "link").symlink_to(self.root)
        for path in ("../outside.txt", "run/../outside.txt", "run//etc/passwd",
                     "run/link/outside.txt", "/etc/passwd"):
            self.assertFalse(call(bench, "read_file", path)[0], path)
            self.assertFalse(call(bench, "write_file", path, "x")[0], path)
        self.assertEqual(call(bench, "search_files", content="secret")[1], "(no matches)")
        self.assertEqual((self.root / "outside.txt").read_text(), "secret")

    def test_listing_never_follows_links(self):
        run = self.root / "run"
        (run / "outside").symlink_to(self.root)
        (run / "loop").symlink_to(run)
        (run / "alias").symlink_to(self.root / "outside.txt")
        (run / "real.txt").write_text("x")
        self.assertEqual(self.bench("architect").files(), ["run/real.txt"])

    def test_a_pipe_is_refused_instead_of_hanging_the_run(self):
        os.mkfifo(self.root / "run" / "pipe")
        bench = self.bench("architect")
        self.assertFalse(call(bench, "read_file", "run/pipe")[0])
        self.assertFalse(call(bench, "write_file", "run/pipe", "x")[0])

    def test_a_workspace_replaced_by_a_link_is_refused(self):
        (self.root / "run").rmdir()
        (self.root / "run").symlink_to(self.root)
        bench = self.bench("architect")
        self.assertFalse(call(bench, "read_file", "run/outside.txt")[0])


class ProfileNameTests(TestCase):
    def test_profiles_may_not_share_a_title(self):
        for title in ("Architect", "ARCHITECT!", "Security Review"):
            with TemporaryDirectory() as root:
                roles = Path(root)
                (roles / "architect.md").write_text("# Architect\n\nDesign.\n")
                (roles / "second.md").write_text(f"# {title}\n\nAlso design.\n")
                with patch("team_agents.profiles.ROLES_DIR", roles), \
                     self.assertRaisesRegex(ValueError, "second.md needs a different title"):
                    load_profiles()


@skipUnless(sandbox.available(), "needs the macOS sandbox")
class SandboxedShellTests(Workspaces):
    def run_command(self, command):
        return call(self.bench("architect", "sandbox"), "run_command", command=command)[1]

    def test_commands_work_in_both_workspaces_only(self):
        self.assertIn("exit 0\nhi", self.run_command('echo hi > "$RUN_WORKSPACE/a" && cat ../run/a'))
        for command in ("cat ../outside.txt", "echo x > ../outside.txt", "ls /Users",
                        "cat ../reviewer/x"):
            self.assertIn("not permitted", self.run_command(command), command)
        self.run_command("cd ..; rm -f run/a; rmdir run; ln -s /tmp run")
        self.assertFalse((self.root / "run").is_symlink())
        self.assertEqual((self.root / "outside.txt").read_text(), "secret")

    def test_commands_get_no_host_environment_or_local_services(self):
        import socket
        with patch.dict(os.environ, {"LAB_SENTINEL": "leaked"}):
            self.assertNotIn("leaked", self.run_command("env"))
        with socket.create_server(("127.0.0.1", 0)) as server:
            port = server.getsockname()[1]
            self.assertNotIn("exit 0", self.run_command(f"curl -sS -m 3 http://127.0.0.1:{port}/"))

    def test_commands_cannot_use_local_sockets_or_link_outside_files_in(self):
        import socket
        path = str(self.root / "s")
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(path)
            server.listen()
            out = self.run_command(
                "python3 -c 'import socket; socket.socket(socket.AF_UNIX).connect("
                f"\"{path}\"); print(\"CONNECTED\")'")
        self.assertIn("not permitted", out)
        self.assertNotIn("exit 0", self.run_command("ln ../outside.txt hard && cat hard"))
