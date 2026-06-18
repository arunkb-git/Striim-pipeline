"""Tests for the TQL validator."""

from unittest.mock import patch, mock_open

from striim_deploy.core.validator import TQLValidator


class TestTQLValidator:
    """Test the TQLValidator class."""

    def test_initialization(self, mock_settings):
        """Test validator initialization."""
        validator = TQLValidator(mock_settings)
        assert validator.settings == mock_settings

    # --- extract_app_identifier -------------------------------------------

    def test_extract_app_identifier_success(self, mock_settings, sample_tql_file):
        """Test successfully extracting the application identifier."""
        validator = TQLValidator(mock_settings)
        assert validator.extract_app_identifier(sample_tql_file) == "TestApp"

    def test_extract_app_identifier_with_namespace(self, mock_settings):
        """The raw identifier keeps any namespace prefix as written."""
        tql_content = """
        CREATE APPLICATION test.MyApp;
        CREATE SOURCE TestSource USING FileReader (file: 'input.txt');
        END APPLICATION test.MyApp;
        """
        with patch("builtins.open", mock_open(read_data=tql_content)):
            with patch("os.path.exists", return_value=True):
                validator = TQLValidator(mock_settings)
                assert validator.extract_app_identifier("test.tql") == "test.MyApp"

    def test_extract_app_identifier_with_create_or_replace(self, mock_settings):
        """Test extracting the identifier with CREATE OR REPLACE."""
        tql_content = """
        CREATE OR REPLACE APPLICATION MyApp;
        CREATE SOURCE TestSource USING FileReader (file: 'input.txt');
        END APPLICATION MyApp;
        """
        with patch("builtins.open", mock_open(read_data=tql_content)):
            with patch("os.path.exists", return_value=True):
                validator = TQLValidator(mock_settings)
                assert validator.extract_app_identifier("test.tql") == "MyApp"

    def test_extract_app_identifier_failure(self, mock_settings):
        """Test failures to extract the application identifier."""
        # No CREATE APPLICATION statement
        with patch(
            "builtins.open",
            mock_open(read_data="CREATE SOURCE S USING FileReader (file: 'x');"),
        ):
            with patch("os.path.exists", return_value=True):
                validator = TQLValidator(mock_settings)
                assert validator.extract_app_identifier("test.tql") is None

        # File not found
        with patch("builtins.open", side_effect=FileNotFoundError()):
            validator = TQLValidator(mock_settings)
            assert validator.extract_app_identifier("nonexistent.tql") is None

        # Invalid regex pattern is caught
        mock_settings.get_application_patterns.return_value = ["[invalid regex"]
        with patch(
            "builtins.open", mock_open(read_data="CREATE APPLICATION TestApp;")
        ):
            validator = TQLValidator(mock_settings)
            assert validator.extract_app_identifier("test.tql") is None

    # --- validate_filename ------------------------------------------------

    def test_validate_filename_match(self, mock_settings):
        validator = TQLValidator(mock_settings)
        assert validator.validate_filename("TestApp.tql", "TestApp") is True

    def test_validate_filename_match_with_suffix(self, mock_settings):
        validator = TQLValidator(mock_settings)
        assert (
            validator.validate_filename(
                "src_SqlServer_IL_Un_Pwd.tql", "src_SqlServer_IL"
            )
            is True
        )

    def test_validate_filename_mismatch_error(self, mock_settings):
        mock_settings.filename_mismatch = "error"
        validator = TQLValidator(mock_settings)
        assert validator.validate_filename("WrongName.tql", "TestApp") is False

    def test_validate_filename_mismatch_warning(self, mock_settings):
        mock_settings.filename_mismatch = "warning"
        validator = TQLValidator(mock_settings)
        assert validator.validate_filename("WrongName.tql", "TestApp") is True

    # --- preprocess_tql_content -------------------------------------------

    def test_preprocess_no_changes(self, mock_settings):
        tql = """
        CREATE OR REPLACE APPLICATION TestApp;
        END APPLICATION TestApp;
        """
        validator = TQLValidator(mock_settings)
        assert validator.preprocess_tql_content(tql) == tql

    def test_preprocess_auto_replace(self, mock_settings):
        tql = """
        CREATE APPLICATION TestApp;
        END APPLICATION TestApp;
        """
        mock_settings.enforce_create_or_replace = True
        mock_settings.create_or_replace_strategy = "auto"
        validator = TQLValidator(mock_settings)
        result = validator.preprocess_tql_content(tql)

        assert "CREATE OR REPLACE APPLICATION" in result
        assert "CREATE APPLICATION" not in result

    def test_preprocess_require_mode(self, mock_settings):
        mock_settings.enforce_create_or_replace = True
        mock_settings.create_or_replace_strategy = "require"
        validator = TQLValidator(mock_settings)

        # Missing OR REPLACE fails
        assert validator.preprocess_tql_content("CREATE APPLICATION TestApp;") is None

        # Present OR REPLACE passes through
        tql = "CREATE OR REPLACE APPLICATION TestApp;"
        assert validator.preprocess_tql_content(tql) == tql

    def test_preprocess_disabled(self, mock_settings):
        tql = "CREATE APPLICATION TestApp;"
        mock_settings.enforce_create_or_replace = False
        validator = TQLValidator(mock_settings)
        result = validator.preprocess_tql_content(tql)

        assert result == tql
        assert "CREATE OR REPLACE APPLICATION" not in result

    # --- validate_naming_convention ---------------------------------------

    def test_naming_convention_valid(self, mock_settings):
        validator = TQLValidator(mock_settings)
        assert validator.validate_naming_convention("striim/TQL/TestApp.tql") is True

    def test_naming_convention_invalid(self, mock_settings):
        validator = TQLValidator(mock_settings)
        assert validator.validate_naming_convention("striim/TQL/1Invalid.tql") is False

    def test_naming_convention_disabled(self, mock_settings):
        mock_settings.enforce_naming_convention = False
        validator = TQLValidator(mock_settings)
        assert validator.validate_naming_convention("striim/TQL/1Invalid.tql") is True

    # --- validate_syntax ---------------------------------------------------

    def test_syntax_balanced(self, mock_settings):
        validator = TQLValidator(mock_settings)
        tql = "CREATE APPLICATION A; ... END APPLICATION A;"
        assert validator.validate_syntax(tql) is True

    def test_syntax_unbalanced(self, mock_settings):
        validator = TQLValidator(mock_settings)
        assert validator.validate_syntax("CREATE APPLICATION A; -- no end") is False

    def test_syntax_no_application(self, mock_settings):
        validator = TQLValidator(mock_settings)
        assert validator.validate_syntax("CREATE SOURCE S USING X ();") is False

    def test_syntax_empty(self, mock_settings):
        validator = TQLValidator(mock_settings)
        assert validator.validate_syntax("   ") is False

    def test_syntax_disabled(self, mock_settings):
        mock_settings.validate_syntax = False
        validator = TQLValidator(mock_settings)
        # Even clearly broken content passes when validation is off.
        assert validator.validate_syntax("CREATE APPLICATION A; -- no end") is True
