"""Tests for token savings estimation."""


from memee.engine.tokens import (
    estimate_org_savings,
    estimate_per_event_savings,
    format_savings_report,
)


class TestPerEventSavings:

    def test_simple_question(self):
        result = estimate_per_event_savings("simple_question", "claude-sonnet-4")
        assert result["without"]["total"] == 3000
        assert result["with_memee"]["total"] == 500
        assert result["saved"]["total"] == 2500
        assert result["reduction_pct"] > 80

    def test_debugging_saves_more(self):
        simple = estimate_per_event_savings("simple_question")
        debug = estimate_per_event_savings("debugging")
        assert debug["saved"]["total"] > simple["saved"]["total"]

    def test_cost_saved_positive(self):
        result = estimate_per_event_savings("research", "gpt-4o")
        assert result["cost_saved"] > 0

    def test_cheap_model_less_savings(self):
        expensive = estimate_per_event_savings("simple_question", "claude-opus-4")
        cheap = estimate_per_event_savings("simple_question", "gemini-2.0-flash")
        assert expensive["cost_saved"] > cheap["cost_saved"]


class TestOrgSavings:

    def test_solo_dev(self):
        savings = estimate_org_savings(agents=1)
        assert savings.total_tokens_saved > 0
        assert savings.total_cost_saved_usd > 0
        assert savings.reduction_pct > 80

    def test_team(self):
        savings = estimate_org_savings(agents=10)
        solo = estimate_org_savings(agents=1)
        assert savings.total_tokens_saved > solo.total_tokens_saved * 5

    def test_enterprise(self):
        savings = estimate_org_savings(agents=200)
        assert savings.total_tokens_saved > 100_000_000  # > 100M tokens
        assert savings.total_cost_saved_usd > 1000

    def test_scale_comparison(self):
        """More agents = proportionally more savings."""
        s10 = estimate_org_savings(agents=10)
        s50 = estimate_org_savings(agents=50)
        assert s50.total_tokens_saved > s10.total_tokens_saved * 3


class TestFormattedReport:

    def test_report_prints(self, capsys):
        savings = estimate_org_savings(agents=10)
        report = format_savings_report(savings, agents=10)
        print(report)
        captured = capsys.readouterr()
        assert "TOKEN SAVINGS" in captured.out
        assert "Solo dev" in captured.out
        assert "Enterprise" in captured.out

    def test_full_report(self):
        """Print full report for visual inspection."""
        savings = estimate_org_savings(agents=50, model="claude-sonnet-4")
        report = format_savings_report(savings, agents=50)
        print(report)
        assert "TOKEN SAVINGS" in report
