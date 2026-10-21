"""Stats already finished on the Training Progression panel are passed over unclicked."""
from unittest.mock import Mock

import pytest

import xynmacro_core as core
from test_fix_batch3b import _controller_setup


def _panel(readings, monkeypatch):
    monkeypatch.setattr(core, "_capture_points_panels", lambda *args: ["a", "b"])
    monkeypatch.setattr(core, "_record_points_target", Mock())
    monkeypatch.setattr(core, "_read_training_points", lambda panel, stat: readings[stat])


def test_start_skips_a_finished_stat_and_clicks_the_next(monkeypatch):
    click = _controller_setup(monkeypatch)
    _panel({"Health": (20, 20, 4), "Agility": (3, 20, 5)}, monkeypatch)
    clicked = []

    def point(stat, *args):
        clicked.append(stat)
        raise core.QuitException()

    monkeypatch.setattr(core, "_button_screen_point", point)
    monkeypatch.setattr(core, "_record_run_outcome", Mock())
    with pytest.raises(core.QuitException):
        core.run_master_controller()
    assert clicked == ["Agility"]
    click.assert_not_called()


@pytest.mark.parametrize("health, outcome", [
    ((20, 20, 0), "completed"),     # full
    ((10, 20, 0), "incomplete"),    # out of Points Left
])
def test_start_with_every_stat_done_clicks_nothing(monkeypatch, health, outcome):
    click = _controller_setup(monkeypatch)
    _panel({"Health": health, "Agility": (20, 20, 0)}, monkeypatch)
    record = Mock()
    monkeypatch.setattr(core, "_record_run_outcome", record)
    core.run_master_controller()
    click.assert_not_called()
    assert record.call_args.args[0] == outcome


def test_unreadable_panel_still_clicks_the_stat(monkeypatch):
    _controller_setup(monkeypatch)
    _panel({"Health": None, "Agility": None}, monkeypatch)
    clicked = []

    def point(stat, *args):
        clicked.append(stat)
        raise core.QuitException()

    monkeypatch.setattr(core, "_button_screen_point", point)
    monkeypatch.setattr(core, "_record_run_outcome", Mock())
    with pytest.raises(core.QuitException):
        core.run_master_controller()
    assert clicked == ["Health"]
