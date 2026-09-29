"""Root package for Python tests.

Importing this package configures the process-wide tempfile default before
unittest imports individual test modules. Pytest gets the same setup through
tests/conftest.py.
"""

from tests.support.temp_paths import configure_test_temp_root


configure_test_temp_root()
