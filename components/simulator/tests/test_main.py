"""Pruebas de los auxiliares del punto de entrada."""

from argparse import Namespace

from simulator.main import athlete_specs


def _args(**kwargs):
    base = {"athletes": 1, "athlete_id": "atleta-001",
            "athlete_prefix": "atleta", "seed": None}
    base.update(kwargs)
    return Namespace(**base)


def test_un_atleta_respeta_el_id_y_la_semilla():
    assert athlete_specs(_args(athlete_id="corredor-7", seed=99)) == [("corredor-7", 99)]


def test_varios_atletas_se_numeran_con_el_prefijo():
    specs = athlete_specs(_args(athletes=3, athlete_prefix="elite"))
    assert [s[0] for s in specs] == ["elite-001", "elite-002", "elite-003"]


def test_semilla_base_se_desplaza_por_atleta():
    specs = athlete_specs(_args(athletes=3, seed=100))
    assert [s[1] for s in specs] == [101, 102, 103]


def test_sin_semilla_todos_aleatorios():
    specs = athlete_specs(_args(athletes=2))
    assert [s[1] for s in specs] == [None, None]
