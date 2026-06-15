import json

from typer.testing import CliRunner

from tgdl import __version__
from tgdl.cli import app

runner = CliRunner()


def test_version_command_json():
    result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["version"] == __version__


def test_version_command_plain():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
