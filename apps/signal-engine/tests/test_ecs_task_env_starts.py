"""Each Fargate task's REAL environment must produce a valid Settings() in prod.

Unit tests run with STAGE unset, which skips Settings' production validator
entirely — so a task definition missing a flag the validator needs passes every
test and then exits 1 on Fargate. That happened to the US task on 2026-09-24
(no MOMENTUM_SCANNER_ONLY). This parses infrastructure/ecs-scanner.yml, takes
each container's literal Environment, adds what SSM injects (a non-default
Mongo URI), sets STAGE=prod, and builds Settings exactly as the task would.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.config import Settings

TEMPLATE = Path(__file__).resolve().parents[3] / "infrastructure" / "ecs-scanner.yml"


def _loader() -> type[yaml.SafeLoader]:
    class L(yaml.SafeLoader):
        pass

    def cfn(loader, suffix, node):
        if isinstance(node, yaml.ScalarNode):
            return {"!" + suffix: loader.construct_scalar(node)}
        if isinstance(node, yaml.SequenceNode):
            return {"!" + suffix: loader.construct_sequence(node, deep=True)}
        return {"!" + suffix: loader.construct_mapping(node, deep=True)}

    L.add_multi_constructor("!", cfn)
    return L


def _task_envs() -> dict[str, dict[str, str]]:
    doc = yaml.load(TEMPLATE.read_text(), Loader=_loader())
    envs = {}
    for name, res in doc["Resources"].items():
        if res["Type"] != "AWS::ECS::TaskDefinition":
            continue
        container = res["Properties"]["ContainerDefinitions"][0]
        envs[name] = {e["Name"]: e["Value"] for e in container["Environment"]
                      if isinstance(e["Value"], str)}          # skip !Ref values
    return envs


TASKS = _task_envs()


def test_template_defines_both_scanner_tasks():
    assert {"TaskDefinition", "UsTaskDefinition"} <= set(TASKS)


@pytest.mark.parametrize("task", sorted(TASKS))
def test_task_environment_passes_the_production_validator(task, monkeypatch):
    for key, value in TASKS[task].items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("STAGE", "prod")
    # Injected at runtime from SSM in the real task; any non-default value.
    monkeypatch.setenv("MONGODB_URI", "mongodb+srv://ci:not-the-default@example.invalid/db")
    Settings(_env_file=None)          # raises if the task would refuse to start
