from smm_gpt.services.retrieval_eval import score


def test_metrics_include_negative_and_invalid_sources() -> None:
    good = score({"a"}, ["a", "a"], {"a"})
    assert good.precision == good.recall == good.citation_validity == 1
    bad = score({"a", "b"}, ["a", "foreign"], {"a", "b"})
    assert bad.precision == bad.recall == bad.citation_validity == 0.5
    assert score(set(), [], {"a"}).negative_pass
    assert not score(set(), ["a"], {"a"}).negative_pass
