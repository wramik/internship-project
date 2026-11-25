import sys
from unittest.mock import patch

import enrich_companies


def test_main_continues_after_company_error(monkeypatch):
    companies = [
        (1, "Company One", "https://one.example"),
        (2, "Company Two", "https://two.example"),
    ]

    processed = []

    monkeypatch.setattr(
        enrich_companies,
        "get_companies",
        lambda **kwargs: companies
    )

    def fake_process_company(company):
        processed.append(company[1])

        if company[1] == "Company One":
            raise RuntimeError("Test error")

        return 0

    monkeypatch.setattr(
        enrich_companies,
        "process_company",
        fake_process_company
    )

    monkeypatch.setattr(
        enrich_companies,
        "save_company_data",
        lambda *args, **kwargs: None
    )

    monkeypatch.setattr(sys, "argv", ["enrich_companies.py"])

    enrich_companies.main()

    assert processed == ["Company One", "Company Two"]
