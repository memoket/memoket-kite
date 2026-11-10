"""Copy the checkout's agent resources into binary distributions."""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def _resource_outputs(self):
        patterns = (
            "AGENTS.md",
            "AGENT_GUIDE.md",
            "CLAUDE.md",
            "CONTRIBUTING.md",
            "pipeline_defs/*.yaml",
            "schemas/*.json",
            "skills/**/SKILL.md",
            "examples/agent-workflows/*.json",
            "examples/agent-workflows/*.md",
            "examples/agent-workflows/incident-log/*.json",
            "examples/agent-workflows/incident-log/connector.py",
            "examples/agent-workflows/incident-log/pipeline_defs/*.yaml",
            "examples/agent-workflows/incident-log/skills/**/SKILL.md",
        )
        destination = Path(self.build_lib) / "memoket_kite" / "_agent_assets"
        return {
            str(destination / source): str(source)
            for pattern in patterns
            for source in sorted(Path(".").glob(pattern))
        }

    def run(self):
        super().run()
        if not self.editable_mode:
            for target, source in self._resource_outputs().items():
                self.mkpath(str(Path(target).parent))
                self.copy_file(source, target)

    def get_outputs(self, include_bytecode=1):
        return [*super().get_outputs(include_bytecode), *self._resource_outputs()]

    def get_source_files(self):
        return [*super().get_source_files(), *self._resource_outputs().values()]

    def get_output_mapping(self):
        return {**super().get_output_mapping(), **self._resource_outputs()}


setup(cmdclass={"build_py": BuildPy})
