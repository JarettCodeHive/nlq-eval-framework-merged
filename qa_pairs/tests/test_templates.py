"""Template + serialization + families.json unit tests (no dataset needed)."""

import decimal

import pytest
import scale_pairs as sp
from jinja2 import meta

from utils.sampling import combos, even_sample
from utils.serialization import SerializationError, serialize
from utils.sql import sqlstr

# ---- serialization ---------------------------------------------------


def test_money_column_keeps_two_places():
    assert serialize([(decimal.Decimal("23184584.00"),)], ["total_budget"]) == "23184584.00"


def test_count_is_plain_integer():
    assert serialize([(1235,)], ["campaign_count"]) == "1235"


def test_percent_two_places():
    assert serialize([(-52.333,)], ["pct_change"]) == "-52.33"


def test_multi_row_join():
    assert (
        serialize([("Awareness", 100), ("Nurture", 90)], ["campaign_type", "pts"])
        == "Awareness | 100; Nurture | 90"
    )


def test_null_scalar_is_blocked():
    with pytest.raises(SerializationError):
        serialize([(None,)], ["x"])


def test_zero_rows_is_blocked():
    with pytest.raises(SerializationError):
        serialize([], ["x"])


# ---- safe SQL literal quoting --------------------------------------


def test_sqlstr_escapes_apostrophe():
    assert sqlstr("O'Brien Ltd") == "'O''Brien Ltd'"


def test_sqlstr_none_is_null():
    assert sqlstr(None) == "NULL"


def test_no_template_bare_interpolates_a_string_literal():
    """Every string param must go through | sqlstr, never '{{ x }}'."""
    for p in (sp.GEN / "templates").rglob("*.sql.j2"):
        src = p.read_text()
        assert "'{{ " not in src, f"{p.name} bare-interpolates a value"


# ---- families.json ------------------------------------------------


def test_family_quotas_sum_to_tier_quota():
    for tier, want in sp.CONFIG["tier_quota"].items():
        got = sum(f["quota"] for f in sp.FAMILIES if f["tier"] == tier)
        assert got == want, (tier, got, want)


def test_every_template_var_is_declared_by_its_family():
    for fam in sp.FAMILIES:
        src = sp.ENV.loader.get_source(sp.ENV, fam["template"])[0]
        required = meta.find_undeclared_variables(sp.ENV.parse(src))
        assert required <= set(fam["params"]), (fam["family"], required, fam["params"])


def test_every_family_has_required_fields():
    need = {
        "family",
        "tier",
        "template",
        "params",
        "quota",
        "join_path",
        "question",
        "tables",
        "fields",
        "judge",
        "rationale",
    }
    for fam in sp.FAMILIES:
        assert need <= set(fam), (fam.get("family"), need - set(fam))


def test_t4_families_are_grouped_aggregations():
    for fam in sp.FAMILIES:
        if fam["tier"] == "T4":
            src = sp.ENV.loader.get_source(sp.ENV, fam["template"])[0]
            assert "GROUP BY" in src, fam["family"]


def test_t4_families_use_all_three_tables():
    for fam in sp.FAMILIES:
        if fam["tier"] == "T4":
            assert len(fam["tables"].split(",")) == 3, fam["family"]
            src = sp.ENV.loader.get_source(sp.ENV, fam["template"])[0].count("INNER JOIN")
            assert src == 2, (fam["family"], "expected 2 INNER JOINs")


def test_attribution_weight_null_imperfection_is_exercised():
    assert [f for f in sp.FAMILIES if "attribution_weight" in f["fields"]]


def test_no_question_template_assumes_growth_direction():
    banned = ("grew", "grew fastest", "growth")
    for fam in sp.FAMILIES:
        q = fam["question"].lower()
        assert not any(b in q for b in banned), (fam["family"], fam["question"])


# ---- sampling -----------------------------------------------------


def test_even_sample_includes_endpoints():
    got = even_sample(list("ABCDEFG"), 3)
    assert got[0] == "A" and got[-1] == "G" and len(got) == 3


def test_even_sample_returns_all_when_k_ge_n():
    assert even_sample([1, 2, 3], 5) == [1, 2, 3]


def test_combos_handles_three_params():
    out = combos({"a": [1, 2], "b": ["x"], "c": [True, False]})
    assert len(out) == 4 and out[0] == {"a": 1, "b": "x", "c": True}
