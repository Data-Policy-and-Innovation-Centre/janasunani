import janasunani.pii.ensemble as ens


def _members():
    return [
        {"model": "claude-3.5", "vendor": "anthropic", "spans": [
            {"start": 0, "end": 5, "entity": "NAME"},
            {"start": 10, "end": 20, "entity": "PHONE"},
            {"start": 30, "end": 40, "entity": "EMAIL"},
        ]},
        {"model": "gpt-4o", "vendor": "openai", "spans": [
            {"start": 0, "end": 5, "entity": "NAME"},  # unanimous
            {"start": 10, "end": 20, "entity": "PHONE"},  # unanimous
            # missing EMAIL — contested
            {"start": 50, "end": 60, "entity": "AADHAAR"},  # contested
        ]},
    ]


def test_union_is_recall_favouring_and_sorted():
    u = ens.union_spans(_members())
    # 4 distinct triples (NAME, PHONE, EMAIL, AADHAAR)
    assert len(u) == 4
    assert u == sorted(u, key=lambda x: (x["start"], x["end"], x["entity"]))


def test_agreement_report_and_claim():
    rep = ens.agreement_report(_members())
    assert rep.n_members == 2
    assert set(rep.vendors) == {"anthropic", "openai"}
    assert rep.total_union == 4
    assert rep.unanimous == 2
    assert rep.contested == 2
    assert rep.agreement_rate == 0.5
    assert rep.per_entity["NAME"]["unanimous"] == 1
    assert rep.per_entity["EMAIL"]["contested"] == 1
    s = ens.claim_sentence(rep, 20)
    assert "2 independent" in s and "20-page" in s


def test_adjudication_queue_is_contested_only():
    q = ens.adjudication_queue(_members())
    assert len(q) == 2
    assert all(r["status"] == "needs_adjudication" for r in q)
    # EMAIL and AADHAAR only
    assert {r["entity"] for r in q} == {"EMAIL", "AADHAAR"}


def test_human_sample_bounds_all_missed_and_deterministic():
    pages = [f"p{i}" for i in range(30)]
    a = ens.human_verification_sample(pages, n=20, seed=7)
    b = ens.human_verification_sample(pages, n=20, seed=7)
    assert a == b
    assert len(a) == 20
    assert len(set(a)) == 20
    # 15-20 guard
    try:
        ens.human_verification_sample(pages, n=14)
        assert False, "should reject"
    except ValueError:
        pass


def test_does_not_use_presidio_draft_as_gold():
    # The draft is detect_pii_spans output; ensemble must not import or call it.
    src = (ens.__file__)
    import pathlib
    text = pathlib.Path(src).read_text()
    assert "detect_pii_spans" not in text
    assert "bootstrap_pii_gold" not in text


def test_spans_are_upper_normalized():
    m = [{"model": "x", "vendor": "v", "spans": [{"start": 0, "end": 1, "entity": "name"}]}]
    assert ens.union_spans(m)[0]["entity"] == "NAME"
