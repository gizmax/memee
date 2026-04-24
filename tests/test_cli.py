"""Tests for CLI commands."""


from click.testing import CliRunner

from memee.cli import cli


def _patch_db(tmp_path):
    """Patch settings.db_path for isolated CLI tests."""
    db_path = tmp_path / "test.db"
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    return db_path


def test_init(tmp_path):
    """memee init creates database and organization."""
    db_path = _patch_db(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    assert "Memee initialized" in result.output
    assert db_path.exists()


def test_init_idempotent(tmp_path):
    """Running init twice doesn't crash."""
    _patch_db(tmp_path)

    runner = CliRunner()
    runner.invoke(cli, ["init"])
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    assert "already exists" in result.output


def test_record_and_search(tmp_path):
    """Record a memory and find it via search."""
    _patch_db(tmp_path)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    # Record (content must differ from title and be 15+ chars)
    result = runner.invoke(
        cli,
        ["record", "pattern", "Use timeout on API calls",
         "-t", "python,api",
         "-c", "Set requests.get(url, timeout=10) to prevent hanging connections"],
    )
    assert result.exit_code == 0
    assert "Recorded [pattern]" in result.output

    # Search
    result = runner.invoke(cli, ["search", "timeout API"])
    assert result.exit_code == 0
    assert "timeout" in result.output.lower()


def test_decide(tmp_path):
    """Record a decision."""
    _patch_db(tmp_path)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(
        cli,
        ["decide", "SQLite", "--over", "PostgreSQL,MongoDB", "-r", "Simpler for MVP"],
    )
    assert result.exit_code == 0
    assert "Decision recorded: SQLite" in result.output


def test_warn_and_check(tmp_path):
    """Record an anti-pattern and check against it."""
    _patch_db(tmp_path)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    # Warn
    result = runner.invoke(
        cli,
        [
            "warn", "Don't use pypdf for complex PDFs",
            "--trigger", "Processing multi-column PDF layouts",
            "--consequence", "Garbled text output",
            "-a", "Use pymupdf or pdfplumber",
            "-s", "high",
            "-t", "python,pdf",
        ],
    )
    assert result.exit_code == 0
    assert "Anti-pattern [!!]" in result.output

    # Check
    result = runner.invoke(cli, ["check", "PDF processing python"])
    assert result.exit_code == 0


def test_status_empty(tmp_path):
    """Status on empty DB shows helpful message."""
    _patch_db(tmp_path)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "empty" in result.output.lower() or "DASHBOARD" in result.output


def test_status_with_data(tmp_path):
    """Status shows dashboard with data."""
    _patch_db(tmp_path)

    runner = CliRunner()
    runner.invoke(cli, ["init"])
    runner.invoke(cli, ["record", "pattern", "Test pattern for dashboard display",
                        "-c", "This is a detailed content about the test pattern", "-t", "test"])
    runner.invoke(cli, ["record", "lesson", "Test lesson for dashboard display",
                        "-c", "This is a detailed content about the lesson learned", "-t", "test"])

    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "DASHBOARD" in result.output
    assert "Total memories:" in result.output


def test_project_register(tmp_path):
    """Register a project."""
    _patch_db(tmp_path)
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(
        cli,
        ["project", "register", str(project_dir), "-n", "MyProject", "-s", "Python,FastAPI"],
    )
    assert result.exit_code == 0
    assert "Registered project: MyProject" in result.output
