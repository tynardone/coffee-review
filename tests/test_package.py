import importlib


def test_package_imports() -> None:
    module = importlib.import_module("coffee_review")

    assert module.__name__ == "coffee_review"
